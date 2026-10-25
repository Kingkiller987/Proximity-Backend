import os
import struct
from datetime import datetime, timezone, timedelta
from typing import Optional, Tuple
from uuid import UUID
import jwt
from fastapi import HTTPException, status
from sqlalchemy.orm import Session
from sqlalchemy import or_, and_, func

from app.config import get_settings
from app.models import MediaObject, LandmarkVisit, UserBlock, EventParticipant, Event, User

settings = get_settings()


def detect_mime_type(data: bytes) -> Tuple[str, str]:
    """Detect MIME type and file extension from magic bytes."""
    if data.startswith(b"\xFF\xD8\xFF"):
        return "image/jpeg", "jpg"
    elif data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png", "png"
    elif data.startswith(b"RIFF") and len(data) >= 12 and data[8:12] == b"WEBP":
        return "image/webp", "webp"
    return "", ""


def parse_image_dimensions(data: bytes, mime_type: str) -> Tuple[int, int]:
    """Parse image (width, height) without external imaging libraries."""
    try:
        if mime_type == "image/png" and len(data) >= 24:
            width, height = struct.unpack(">II", data[16:24])
            return width, height
        elif mime_type == "image/jpeg":
            idx = 2
            while idx < len(data) - 9:
                if data[idx] != 0xFF:
                    break
                marker = data[idx + 1]
                if marker in (0xC0, 0xC1, 0xC2):
                    height, width = struct.unpack(">HH", data[idx + 5 : idx + 9])
                    return width, height
                elif marker in (0xD9, 0xDA):
                    break
                else:
                    length = struct.unpack(">H", data[idx + 2 : idx + 4])[0]
                    idx += 2 + length
        elif mime_type == "image/webp" and len(data) >= 30:
            format_tag = data[12:16]
            if format_tag == b"VP8 ":
                w = struct.unpack("<H", data[26:28])[0] & 0x3FFF
                h = struct.unpack("<H", data[28:30])[0] & 0x3FFF
                return w, h
            elif format_tag == b"VP8L":
                b0, b1, b2, b3 = data[21:25]
                w = 1 + (((b1 & 0x3F) << 8) | b0)
                h = 1 + (((b3 & 0x0F) << 10) | (b2 << 2) | ((b1 & 0xC0) >> 6))
                return w, h
            elif format_tag == b"VP8X":
                w = 1 + int.from_bytes(data[24:27], "little")
                h = 1 + int.from_bytes(data[27:30], "little")
                return w, h
    except Exception:
        pass
    return 0, 0


def validate_image_bytes(data: bytes, reported_mime: Optional[str] = None) -> Tuple[str, str, int, int]:
    """Validate image bytes, enforcing <= 5MB, format sniffing, and size constraints."""
    if not data:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Uploaded file is empty",
        )

    if len(data) > settings.MAX_IMAGE_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"File exceeds maximum allowed size of {settings.MAX_IMAGE_BYTES} bytes",
        )

    detected_mime, ext = detect_mime_type(data)
    if not detected_mime:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Unsupported file format. Only JPEG, PNG, and WebP images are allowed.",
        )

    if reported_mime:
        clean_reported = reported_mime.split(";")[0].strip().lower()
        if clean_reported != detected_mime:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Reported MIME type ({reported_mime}) does not match detected format ({detected_mime}).",
            )

    width, height = parse_image_dimensions(data, detected_mime)
    return detected_mime, ext, width, height


def create_media_download_token(user_id: UUID, media_id: UUID, channel_id: str, expires_in_seconds: int = 60) -> Tuple[str, datetime]:
    """Create short-lived signed JWT download token."""
    now_utc = datetime.now(timezone.utc)
    expires_at = now_utc + timedelta(seconds=expires_in_seconds)
    payload = {
        "sub": str(user_id),
        "media_id": str(media_id),
        "channel_id": str(channel_id),
        "type": "media_download",
        "exp": expires_at,
    }
    token = jwt.encode(payload, settings.SECRET_KEY, algorithm="HS256")
    return token, expires_at


def decode_media_download_token(token: str) -> Optional[dict]:
    """Decode and validate media download token."""
    try:
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=["HS256"])
        if payload.get("type") != "media_download":
            return None
        return payload
    except (jwt.PyJWTError, ValueError):
        return None


def check_media_access_authorization(media: MediaObject, user_id: UUID, db: Session) -> bool:
    """Enforce server-authoritative live access permissions for media."""
    now_utc = datetime.now(timezone.utc)

    # 1. Check expiration
    if media.expires_at <= now_utc:
        return False

    # 2. Check bidirectional blocking
    is_blocked = db.query(UserBlock).filter(
        or_(
            and_(UserBlock.blocker_id == user_id, UserBlock.blocked_id == media.sender_id),
            and_(UserBlock.blocker_id == media.sender_id, UserBlock.blocked_id == user_id),
        )
    ).first()
    if is_blocked:
        return False

    # 3. Check requester account and ghost mode status
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        return False

    # 4. Scope-specific rules
    if media.channel_kind == "landmark":
        if user.is_ghost_mode:
            return False

        try:
            lm_id = UUID(media.channel_id)
        except ValueError:
            return False

        visit = db.query(LandmarkVisit).filter(
            LandmarkVisit.user_id == user_id,
            LandmarkVisit.landmark_id == lm_id,
            LandmarkVisit.is_active == True,
        ).first()

        if not visit:
            return False

        # Freshness of visit heartbeat (allowing 45s grace)
        if (now_utc - visit.last_heartbeat_at).total_seconds() > 45.0:
            return False

        # Monotonic sequence & timestamp isolation: only media created during this visit
        if media.created_at < visit.entered_at:
            return False
        if media.sequence < visit.min_message_sequence:
            return False

        return True

    elif media.channel_kind == "event":
        try:
            evt_id = UUID(media.channel_id)
        except ValueError:
            return False

        event = db.query(Event).filter(Event.id == evt_id).first()
        if not event or event.is_cancelled:
            return False

        participant = db.query(EventParticipant).filter(
            EventParticipant.event_id == evt_id,
            EventParticipant.user_id == user_id,
        ).first()
        return participant is not None

    elif media.channel_kind == "dm":
        # Direct messaging media is deferred pending dedicated conversation schema
        return False

    return False
