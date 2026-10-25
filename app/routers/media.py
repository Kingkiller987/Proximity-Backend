import os
import uuid
from datetime import datetime, timezone, timedelta
from typing import Optional
from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile, File, Form, status
from fastapi.responses import Response
from sqlalchemy.orm import Session
from sqlalchemy import or_, and_

from app.database import get_db
from app.models import MediaObject, User, LandmarkVisit, EventParticipant, Event, DmConversation, UserBlock
from app.schemas import MediaUploadResponse, MediaDownloadTokenResponse
from app.dependencies import get_current_user, get_optional_user
from app.config import get_settings
from app.storage import get_storage_backend
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
    if channel_kind not in ("landmark", "event", "dm"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid channel_kind. Must be one of: landmark, event, dm",
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

    elif channel_kind == "dm":
        try:
            conv_uuid = UUID(channel_id)
        except ValueError:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid conversation ID")

        conv = db.query(DmConversation).filter(DmConversation.id == conv_uuid).first()
        if not conv:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="DM conversation not found",
            )
        if current_user.id not in (conv.initiator_id, conv.recipient_id):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Must be a participant of the conversation to upload media.",
            )
        if conv.status != "accepted":
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Direct messaging media is only allowed after the request is accepted.",
            )

        other_id = conv.recipient_id if conv.initiator_id == current_user.id else conv.initiator_id
        is_blocked = db.query(UserBlock).filter(
            or_(
                and_(UserBlock.blocker_id == current_user.id, UserBlock.blocked_id == other_id),
                and_(UserBlock.blocker_id == other_id, UserBlock.blocked_id == current_user.id),
            )
        ).first()
        if is_blocked:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Cannot upload media in conversation with blocked user.",
            )

        assigned_seq = 0

    storage = get_storage_backend()
    media_id = uuid.uuid4()
    storage_key = f"media/{media_id}.{ext}"

    # Atomic step 1: write to storage
    try:
        await storage.save_file(storage_key, content, detected_mime)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to persist media to storage",
        )

    expires_at = now_utc + timedelta(hours=current_user.message_retention_hours)
    media_obj = MediaObject(
        id=media_id,
        storage_path=storage_key,
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

    # Atomic step 2: write to database with rollback & storage cleanup on failure
    try:
        db.add(media_obj)
        db.commit()
        db.refresh(media_obj)
    except Exception as exc:
        db.rollback()
        await storage.delete_file(storage_key)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to save media metadata",
        )

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
async def download_media(
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
    if not media:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Media not found")

    # Enforce authorization BEFORE touching storage
    if not check_media_access_authorization(media, user_id, db):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Unauthorized to access this media",
        )

    storage = get_storage_backend()
    try:
        data, content_type = await storage.get_file(media.storage_path)
    except (FileNotFoundError, ValueError):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Media not found")
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Error retrieving media from storage")

    return Response(content=data, media_type=media.mime_type)
