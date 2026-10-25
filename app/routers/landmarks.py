from datetime import datetime, timezone, timedelta
from typing import List, Optional
from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session
from sqlalchemy import func, and_, or_, text
from app.database import get_db
from app.models import Landmark, LandmarkVisit, ChannelSequence, Message, User, UserBlock, MediaObject
from app.schemas import (
    LandmarkResponse,
    LandmarkVisitResponse,
    LocationFixRequest,
    LandmarkMessageCreateRequest,
    ChatMessageResponse,
)
from app.dependencies import get_current_user, get_optional_user
from app.location import (
    validate_location_fix,
    make_geography_point,
    format_coarse_distance_label,
)

router = APIRouter(prefix="/landmarks", tags=["landmarks"])


@router.get("", response_model=List[LandmarkResponse])
def list_landmarks(
    latitude: Optional[float] = Query(None, ge=-90.0, le=90.0),
    longitude: Optional[float] = Query(None, ge=-180.0, le=180.0),
    db: Session = Depends(get_db),
    current_user: Optional[User] = Depends(get_optional_user),
):
    landmarks = db.query(Landmark).all()
    results = []

    user_pt = None
    if latitude is not None and longitude is not None:
        user_pt = make_geography_point(longitude, latitude)

    for lm in landmarks:
        dist_m = None
        dist_lbl = None
        is_inside = None

        if user_pt is not None:
            row = db.query(
                func.ST_Distance(Landmark.location, user_pt),
                func.ST_DWithin(Landmark.location, user_pt, Landmark.radius_meters),
            ).filter(Landmark.id == lm.id).one()
            dist_m = float(row[0])
            dist_lbl = format_coarse_distance_label(dist_m)
            is_inside = bool(row[1])

        results.append(
            LandmarkResponse(
                id=lm.id,
                name=lm.name,
                area_name=lm.area_name,
                latitude=lm.latitude,
                longitude=lm.longitude,
                radius_meters=lm.radius_meters,
                category=lm.category,
                distance_meters=dist_m,
                distance_label=dist_lbl,
                is_user_inside=is_inside,
            )
        )

    return results


@router.post("/{landmark_id}/enter", response_model=LandmarkVisitResponse)
def enter_landmark(
    landmark_id: UUID,
    fix: LocationFixRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if current_user.is_ghost_mode:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Ghost mode is active. Disable ghost mode to enter landmark chats.",
        )

    validate_location_fix(fix)

    landmark = db.query(Landmark).filter(Landmark.id == landmark_id).first()
    if not landmark:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Landmark not found")

    user_pt = make_geography_point(fix.longitude, fix.latitude)
    is_inside = db.query(
        func.ST_DWithin(Landmark.location, user_pt, Landmark.radius_meters)
    ).filter(Landmark.id == landmark_id).scalar()

    if not is_inside:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Outside authorized landmark radius",
        )

    # Invariant: Transactionally terminate previous visits
    db.query(LandmarkVisit).filter(
        LandmarkVisit.user_id == current_user.id,
        LandmarkVisit.landmark_id == landmark_id,
        LandmarkVisit.is_active == True,
    ).update({"is_active": False, "exit_reason": "new_visit"}, synchronize_session="fetch")

    # Ensure ChannelSequence row exists so with_for_update() locks an actual row
    db.execute(
        text(
            "INSERT INTO channel_sequences (channel_id, current_sequence) "
            "VALUES (:cid, 0) ON CONFLICT (channel_id) DO NOTHING"
        ),
        {"cid": str(landmark_id)},
    )

    seq_row = db.query(ChannelSequence).filter(
        ChannelSequence.channel_id == str(landmark_id)
    ).with_for_update().first()

    curr_seq = seq_row.current_sequence if seq_row else 0

    new_visit = LandmarkVisit(
        user_id=current_user.id,
        landmark_id=landmark_id,
        min_message_sequence=curr_seq + 1,  # Invariant: Only sees messages posted AFTER entry
        is_active=True,
    )
    db.add(new_visit)
    db.commit()
    db.refresh(new_visit)

    return LandmarkVisitResponse(
        session_id=new_visit.id,
        landmark_id=new_visit.landmark_id,
        entered_at=new_visit.entered_at,
        last_heartbeat_at=new_visit.last_heartbeat_at,
        min_message_sequence=new_visit.min_message_sequence,
        is_active=new_visit.is_active,
    )


@router.post("/{landmark_id}/heartbeat", response_model=LandmarkVisitResponse)
def landmark_heartbeat(
    landmark_id: UUID,
    fix: LocationFixRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if current_user.is_ghost_mode:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Ghost mode active")

    validate_location_fix(fix)

    visit = db.query(LandmarkVisit).filter(
        LandmarkVisit.user_id == current_user.id,
        LandmarkVisit.landmark_id == landmark_id,
        LandmarkVisit.is_active == True,
    ).first()

    if not visit:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No active visit session")

    landmark = db.query(Landmark).filter(Landmark.id == landmark_id).first()
    user_pt = make_geography_point(fix.longitude, fix.latitude)
    is_inside = db.query(
        func.ST_DWithin(Landmark.location, user_pt, Landmark.radius_meters)
    ).filter(Landmark.id == landmark_id).scalar()

    if not is_inside:
        visit.is_active = False
        visit.exit_reason = "left_radius"
        db.commit()
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Moved outside landmark radius")

    visit.last_heartbeat_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(visit)

    return LandmarkVisitResponse(
        session_id=visit.id,
        landmark_id=visit.landmark_id,
        entered_at=visit.entered_at,
        last_heartbeat_at=visit.last_heartbeat_at,
        min_message_sequence=visit.min_message_sequence,
        is_active=visit.is_active,
    )


@router.post("/{landmark_id}/leave", status_code=status.HTTP_204_NO_CONTENT)
def leave_landmark(
    landmark_id: UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    db.query(LandmarkVisit).filter(
        LandmarkVisit.user_id == current_user.id,
        LandmarkVisit.landmark_id == landmark_id,
        LandmarkVisit.is_active == True,
    ).update({"is_active": False, "exit_reason": "user_left"}, synchronize_session="fetch")
    db.commit()
    return None


@router.get("/{landmark_id}/messages", response_model=List[ChatMessageResponse])
def get_landmark_messages(
    landmark_id: UUID,
    since_sequence: Optional[int] = Query(None, ge=0),
    limit: int = Query(50, ge=1, le=100),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if current_user.is_ghost_mode:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Ghost mode active")

    visit = db.query(LandmarkVisit).filter(
        LandmarkVisit.user_id == current_user.id,
        LandmarkVisit.landmark_id == landmark_id,
        LandmarkVisit.is_active == True,
    ).first()

    if not visit:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="No active visit session. Enter landmark with fresh GPS to view chat.",
        )

    now_utc = datetime.now(timezone.utc)
    if (now_utc - visit.last_heartbeat_at).total_seconds() > 45.0:
        visit.is_active = False
        visit.exit_reason = "heartbeat_timeout"
        db.commit()
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Visit session expired due to heartbeat timeout",
        )

    blocked_ids = [
        row[0] for row in db.query(UserBlock.blocked_id).filter(UserBlock.blocker_id == current_user.id).all()
    ] + [
        row[0] for row in db.query(UserBlock.blocker_id).filter(UserBlock.blocked_id == current_user.id).all()
    ]

    # Invariant: Query enforces sequence >= min_sequence AND created_at >= entered_at
    # Even if client passes an old cursor (since_sequence < visit.min_message_sequence), boundary is clamped
    effective_min_sequence = visit.min_message_sequence
    if since_sequence is not None:
        effective_min_sequence = max(visit.min_message_sequence, since_sequence + 1)

    query = db.query(Message).filter(
        Message.channel_id == str(landmark_id),
        Message.sequence >= effective_min_sequence,
        Message.created_at >= visit.entered_at,
        Message.expires_at > func.now(),
    )
    if blocked_ids:
        query = query.filter(Message.sender_id.notin_(blocked_ids))

    messages = query.order_by(Message.sequence.asc()).limit(limit).all()

    results = []
    for msg in messages:
        results.append(
            ChatMessageResponse(
                id=msg.id,
                channel_id=msg.channel_id,
                sender_id=msg.sender_id,
                sender_display_name=msg.sender_display_name,
                is_anonymous=msg.is_anonymous,
                body=msg.body,
                sequence=msg.sequence,
                area_name=msg.area_name,
                distance_label="< 100m away",
                attachment_id=msg.attachment_id,
                is_system_notice=msg.is_system_notice,
                created_at=msg.created_at,
                expires_at=msg.expires_at,
            )
        )
    return results


@router.post("/{landmark_id}/messages", response_model=ChatMessageResponse, status_code=status.HTTP_201_CREATED)
def post_landmark_message(
    landmark_id: UUID,
    data: LandmarkMessageCreateRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if current_user.is_ghost_mode:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Ghost mode active")

    validate_location_fix(data.location_fix)

    visit = db.query(LandmarkVisit).filter(
        LandmarkVisit.user_id == current_user.id,
        LandmarkVisit.landmark_id == landmark_id,
        LandmarkVisit.is_active == True,
    ).first()

    if not visit:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="No active visit session")

    now_utc = datetime.now(timezone.utc)
    # Invariant: If visit heartbeat timed out, cannot post without re-entering
    if (now_utc - visit.last_heartbeat_at).total_seconds() > 45.0:
        visit.is_active = False
        visit.exit_reason = "heartbeat_timeout"
        db.commit()
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Visit session expired due to heartbeat timeout. Please re-enter landmark.",
        )

    landmark = db.query(Landmark).filter(Landmark.id == landmark_id).first()
    user_pt = make_geography_point(data.location_fix.longitude, data.location_fix.latitude)
    is_inside = db.query(
        func.ST_DWithin(Landmark.location, user_pt, Landmark.radius_meters)
    ).filter(Landmark.id == landmark_id).scalar()

    if not is_inside:
        visit.is_active = False
        visit.exit_reason = "left_radius"
        db.commit()
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Outside authorized landmark radius")

    # Ensure ChannelSequence row exists so with_for_update() locks an actual row
    db.execute(
        text(
            "INSERT INTO channel_sequences (channel_id, current_sequence) "
            "VALUES (:cid, 0) ON CONFLICT (channel_id) DO NOTHING"
        ),
        {"cid": str(landmark_id)},
    )

    seq_row = db.query(ChannelSequence).filter(
        ChannelSequence.channel_id == str(landmark_id)
    ).with_for_update().first()

    seq_row.current_sequence += 1
    next_seq = seq_row.current_sequence

    # Invariant: Validate attachment media ownership and channel association
    if data.attachment_id:
        media = db.query(MediaObject).filter(MediaObject.id == data.attachment_id).first()
        if not media:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Attachment media not found")
        if media.sender_id != current_user.id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Cannot attach media uploaded by another user",
            )
        if media.channel_kind != "landmark" or media.channel_id != str(landmark_id):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Attachment channel mismatch",
            )
        media.sequence = next_seq

    display_name = current_user.anonymous_handle if current_user.identity_mode == "anonymous" else f"@{current_user.username}"
    is_anon = current_user.identity_mode == "anonymous"

    expires_at = now_utc + timedelta(hours=current_user.message_retention_hours)

    msg = Message(
        channel_id=str(landmark_id),
        channel_kind="landmark",
        sender_id=current_user.id,
        sender_display_name=display_name,
        is_anonymous=is_anon,
        body=data.body,
        sequence=next_seq,
        origin_location=user_pt,
        area_name=landmark.area_name,
        attachment_id=data.attachment_id,
        expires_at=expires_at,
    )
    db.add(msg)
    visit.last_heartbeat_at = now_utc
    db.commit()
    db.refresh(msg)

    return ChatMessageResponse(
        id=msg.id,
        channel_id=msg.channel_id,
        sender_id=msg.sender_id,
        sender_display_name=msg.sender_display_name,
        is_anonymous=msg.is_anonymous,
        body=msg.body,
        sequence=msg.sequence,
        area_name=msg.area_name,
        distance_label="< 100m away",
        attachment_id=msg.attachment_id,
        is_system_notice=msg.is_system_notice,
        created_at=msg.created_at,
        expires_at=msg.expires_at,
    )
