import os
import uuid
from datetime import datetime, timezone, timedelta
from typing import Optional
from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile, File, Form, status
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session
from sqlalchemy import func

from app.database import get_db
from app.models import MediaObject, User, LandmarkVisit, EventParticipant, Event
from app.schemas import MediaUploadResponse, MediaDownloadTokenResponse
from app.dependencies import get_current_user, get_optional_user
from app.config import get_settings
from app.media import (
    validate_image_bytes,
    create_media_download_token,
    decode_media_download_token,
    check_media_access_authorization,
)

settings = get_settings()
router = APIRouter(prefix="/media", tags=["media"])


@router.post("/upload", response_model=MediaUploadResponse, status_code=status.HTTP_201_CREATED)
async def upload_media(
    file: UploadFile = File(...),
    channel_kind: str = Form(...),
    channel_id: str = Form(...),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if channel_kind == "dm":
        raise HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            detail="Direct messaging media is deferred pending dedicated conversation schema",
        )

    if channel_kind not in ("landmark", "event"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid channel_kind. Must be one of: landmark, event",
        )

    content = await file.read()
    detected_mime, ext, width, height = validate_image_bytes(content, reported_mime=file.content_type)

    now_utc = datetime.now(timezone.utc)
    assigned_seq = 0

    if channel_kind == "landmark":
        if current_user.is_ghost_mode:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Ghost mode active. Cannot upload media to landmark chat.",
            )
        try:
            lm_uuid = UUID(channel_id)
        except ValueError:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid landmark ID")

        visit = db.query(LandmarkVisit).filter(
            LandmarkVisit.user_id == current_user.id,
            LandmarkVisit.landmark_id == lm_uuid,
            LandmarkVisit.is_active == True,
        ).first()

        if not visit:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Must have an active landmark visit to upload media.",
            )

        if (now_utc - visit.last_heartbeat_at).total_seconds() > 45.0:
            visit.is_active = False
            visit.exit_reason = "heartbeat_timeout"
            db.commit()
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Landmark visit expired due to heartbeat timeout.",
            )

        assigned_seq = visit.min_message_sequence

    elif channel_kind == "event":
        try:
            evt_uuid = UUID(channel_id)
        except ValueError:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid event ID")

        event = db.query(Event).filter(Event.id == evt_uuid).first()
        if not event or event.is_cancelled:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Event not found or cancelled",
            )

        participant = db.query(EventParticipant).filter(
            EventParticipant.event_id == evt_uuid,
            EventParticipant.user_id == current_user.id,
        ).first()
        if not participant:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Must be a participant of the event to upload media.",
            )

    os.makedirs(settings.MEDIA_STORAGE_DIR, exist_ok=True)
    media_id = uuid.uuid4()
    storage_path = os.path.join(settings.MEDIA_STORAGE_DIR, f"{media_id}.{ext}")

    with open(storage_path, "wb") as f:
        f.write(content)

    expires_at = now_utc + timedelta(hours=current_user.message_retention_hours)
    media_obj = MediaObject(
        id=media_id,
        storage_path=storage_path,
        channel_kind=channel_kind,
        channel_id=channel_id,
        sender_id=current_user.id,
        mime_type=detected_mime,
        size_bytes=len(content),
        width=width,
        height=height,
        sequence=assigned_seq,
        created_at=now_utc,
        expires_at=expires_at,
    )
    db.add(media_obj)
    db.commit()
    db.refresh(media_obj)

    return MediaUploadResponse(
        media_id=media_obj.id,
        mime_type=media_obj.mime_type,
        size_bytes=media_obj.size_bytes,
        width=media_obj.width,
        height=media_obj.height,
    )


@router.post("/{media_id}/token", response_model=MediaDownloadTokenResponse)
def get_media_download_token(
    media_id: UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    media = db.query(MediaObject).filter(MediaObject.id == media_id).first()
    if not media:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Media not found")

    if not check_media_access_authorization(media, current_user.id, db):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Unauthorized to access this media",
        )

    token, expires_at = create_media_download_token(current_user.id, media.id, media.channel_id)
    return MediaDownloadTokenResponse(
        token=token,
        media_id=media.id,
        channel_id=media.channel_id,
        expires_at=expires_at,
    )


@router.get("/{media_id}")
def download_media(
    media_id: UUID,
    token: Optional[str] = Query(None),
    current_user: Optional[User] = Depends(get_optional_user),
    db: Session = Depends(get_db),
):
    user_id = None
    if token:
        payload = decode_media_download_token(token)
        if not payload or payload.get("media_id") != str(media_id):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Invalid or expired download token",
            )
        try:
            user_id = UUID(payload["sub"])
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Malformed token claims",
            )
    elif current_user:
        user_id = current_user.id
    else:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required",
        )

    media = db.query(MediaObject).filter(MediaObject.id == media_id).first()
    if not media or not os.path.exists(media.storage_path):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Media not found")

    # Defense against filesystem path traversal
    clean_media_dir = os.path.abspath(settings.MEDIA_STORAGE_DIR)
    clean_file_path = os.path.abspath(media.storage_path)
    if not clean_file_path.startswith(clean_media_dir + os.sep):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied")

    if not check_media_access_authorization(media, user_id, db):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Unauthorized to access this media",
        )

    return FileResponse(media.storage_path, media_type=media.mime_type)
