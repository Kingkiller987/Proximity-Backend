import uuid
from datetime import datetime, timezone, timedelta
from typing import List, Optional
from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session
from sqlalchemy import func, and_, or_, text

from app.database import get_db
from app.models import Event, EventParticipant, User, Message, ChannelSequence, UserBlock, MediaObject
from app.schemas import (
    EventCreateRequest,
    EventResponse,
    EventJoinRequest,
    EventTransferRequest,
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
from app.schedule import validate_timezone, is_within_operating_window
from app.security import hash_join_code

router = APIRouter(prefix="/events", tags=["events"])


def _format_event_response(
    event: Event,
    current_user_id: Optional[UUID] = None,
    user_pt=None,
    db: Session = None,
) -> EventResponse:
    dist_lbl = None
    if user_pt is not None and db is not None:
        row = db.query(
            func.ST_Distance(Event.venue_location, user_pt)
        ).filter(Event.id == event.id).first()
        if row and row[0] is not None:
            dist_lbl = format_coarse_distance_label(float(row[0]))

    is_joined = False
    is_host = False
    participant_count = 0

    if db is not None:
        participant_count = db.query(func.count(EventParticipant.user_id)).filter(
            EventParticipant.event_id == event.id
        ).scalar() or 0

        if current_user_id:
            is_host = (event.host_id == current_user_id)
            p = db.query(EventParticipant).filter(
                EventParticipant.event_id == event.id,
                EventParticipant.user_id == current_user_id,
            ).first()
            is_joined = p is not None or is_host

    # Privacy redaction: private descriptions are redacted for non-members on restricted events
    desc = event.description
    if event.access_type in ("joinCode", "inviteOnly") and not is_joined and not is_host:
        desc = ""

    overrides_dict = {}
    if event.daily_overrides:
        for k, v in event.daily_overrides.items():
            if hasattr(v, "model_dump"):
                overrides_dict[k] = v.model_dump()
            elif isinstance(v, dict):
                overrides_dict[k] = v

    return EventResponse(
        id=event.id,
        host_id=event.host_id,
        title=event.title,
        description=desc,
        category_tag=event.category_tag,
        area_name=event.area_name,
        radius_meters=event.radius_meters,
        access_type=event.access_type,
        starts_at=event.starts_at,
        expires_at=event.expires_at,
        venue_timezone=event.venue_timezone,
        default_open_minutes=event.default_open_minutes,
        default_close_minutes=event.default_close_minutes,
        daily_overrides=overrides_dict,
        is_cancelled=event.is_cancelled,
        is_ended=event.is_ended,
        is_started=event.is_started,
        started_at=event.started_at,
        participant_count=participant_count if (is_joined or is_host) else None,
        is_joined=is_joined,
        is_host=is_host,
        distance_label=dist_lbl,
        created_at=event.created_at,
    )


@router.post("", response_model=EventResponse, status_code=status.HTTP_201_CREATED)
def create_event(
    data: EventCreateRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    now_utc = datetime.now(timezone.utc)
    if data.starts_at < now_utc - timedelta(minutes=5):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Event start time cannot be in the past",
        )

    validate_timezone(data.venue_timezone)

    event_id = uuid.uuid4()
    venue_pt = make_geography_point(data.longitude, data.latitude)
    expires_at = data.starts_at + timedelta(seconds=data.duration_seconds)

    code_hash = None
    if data.access_type == "joinCode":
        if not data.join_code or len(data.join_code.strip()) < 4:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Join code of at least 4 characters required for joinCode events",
            )
        code_hash = hash_join_code(data.join_code, salt=str(event_id))

    overrides_json = {
        k: v.model_dump() for k, v in data.daily_overrides.items()
    } if data.daily_overrides else {}

    event = Event(
        id=event_id,
        host_id=current_user.id,
        title=data.title,
        description=data.description,
        category_tag=data.category_tag,
        venue_location=venue_pt,
        area_name=data.area_name,
        radius_meters=data.radius_meters,
        access_type=data.access_type,
        join_code_hash=code_hash,
        starts_at=data.starts_at,
        duration_seconds=data.duration_seconds,
        expires_at=expires_at,
        venue_timezone=data.venue_timezone,
        default_open_minutes=data.default_open_minutes,
        default_close_minutes=data.default_close_minutes,
        daily_overrides=overrides_json,
    )
    db.add(event)

    host_part = EventParticipant(
        event_id=event_id,
        user_id=current_user.id,
        role="host",
    )
    db.add(host_part)
    db.commit()
    db.refresh(event)

    return _format_event_response(event, current_user.id, None, db)


@router.get("", response_model=List[EventResponse])
def list_events(
    latitude: Optional[float] = Query(None, ge=-90.0, le=90.0),
    longitude: Optional[float] = Query(None, ge=-180.0, le=180.0),
    current_user: Optional[User] = Depends(get_optional_user),
    db: Session = Depends(get_db),
):
    query = db.query(Event).filter(Event.is_cancelled == False, Event.is_ended == False)

    user_pt = None
    if latitude is not None and longitude is not None:
        user_pt = make_geography_point(longitude, latitude)

    events = query.order_by(Event.starts_at.asc()).all()
    user_id = current_user.id if current_user else None

    results = []
    for evt in events:
        if evt.access_type == "inviteOnly":
            if not user_id:
                continue
            is_part = db.query(EventParticipant).filter(
                EventParticipant.event_id == evt.id,
                EventParticipant.user_id == user_id,
            ).first()
            if not is_part and evt.host_id != user_id:
                continue

        results.append(_format_event_response(evt, user_id, user_pt, db))

    return results


@router.get("/{event_id}", response_model=EventResponse)
def get_event(
    event_id: UUID,
    current_user: Optional[User] = Depends(get_optional_user),
    db: Session = Depends(get_db),
):
    event = db.query(Event).filter(Event.id == event_id).first()
    if not event or event.is_cancelled:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Event not found")

    user_id = current_user.id if current_user else None
    return _format_event_response(event, user_id, None, db)


@router.post("/{event_id}/join")
def join_event(
    event_id: UUID,
    body: EventJoinRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    event = db.query(Event).filter(Event.id == event_id).first()
    if not event or event.is_cancelled:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Event not found")
    if event.is_ended:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Event has already ended")

    existing = db.query(EventParticipant).filter(
        EventParticipant.event_id == event.id,
        EventParticipant.user_id == current_user.id,
    ).first()
    if existing:
        return {"status": "already_joined", "event_id": str(event.id)}

    if event.access_type == "joinCode":
        if not body.join_code:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Join code required")
        expected_hash = hash_join_code(body.join_code, salt=str(event.id))
        if expected_hash != event.join_code_hash:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Incorrect join code")
    elif event.access_type == "inviteOnly":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invite-only event requires prior host invitation",
        )

    participant = EventParticipant(
        event_id=event.id,
        user_id=current_user.id,
        role="attendee",
    )
    db.add(participant)
    db.commit()
    return {"status": "joined", "event_id": str(event.id)}


@router.post("/{event_id}/start", response_model=EventResponse)
def start_event(
    event_id: UUID,
    fix: LocationFixRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    validate_location_fix(fix)

    # Invariant: Concurrency-safe row-level lock
    event = db.query(Event).filter(Event.id == event_id).with_for_update().first()
    if not event or event.is_cancelled:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Event not found")

    if event.host_id != current_user.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Only current host can start event")

    if event.is_ended:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Event has ended")

    now_utc = datetime.now(timezone.utc)

    # Invariant: Reject start if scheduled start time is more than 30m in future or if expired
    if now_utc < event.starts_at - timedelta(minutes=30):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Cannot start event earlier than 30 minutes before scheduled start time ({event.starts_at.isoformat()})",
        )
    if now_utc > event.expires_at:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Event expiration time has already passed ({event.expires_at.isoformat()})",
        )

    if not is_within_operating_window(event, now_utc):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Event cannot start outside venue operating hours",
        )

    # Invariant: Host must be physically inside venue radius
    host_pt = make_geography_point(fix.longitude, fix.latitude)
    is_inside = db.query(
        func.ST_DWithin(Event.venue_location, host_pt, Event.radius_meters)
    ).filter(Event.id == event.id).scalar()

    if not is_inside:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Host must be physically inside event radius to start event",
        )

    event.is_started = True
    event.started_at = now_utc
    db.commit()
    db.refresh(event)

    return _format_event_response(event, current_user.id, host_pt, db)


@router.post("/{event_id}/transfer-host")
def transfer_host_phase1(
    event_id: UUID,
    req: EventTransferRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    event = db.query(Event).filter(Event.id == event_id).with_for_update().first()
    if not event or event.is_cancelled:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Event not found")

    if event.host_id != current_user.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Only current host can initiate transfer")

    if req.recipient_user_id == current_user.id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Cannot transfer to oneself")

    # Invariant: Recipient must be an existing verified participant
    part = db.query(EventParticipant).filter(
        EventParticipant.event_id == event.id,
        EventParticipant.user_id == req.recipient_user_id,
    ).first()
    if not part:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Recipient must be a joined participant of this event",
        )

    event.pending_host_transfer_user_id = req.recipient_user_id
    db.commit()

    return {
        "status": "transfer_initiated",
        "pending_recipient": str(req.recipient_user_id),
    }


@router.post("/{event_id}/accept-transfer", response_model=EventResponse)
def accept_transfer_phase2(
    event_id: UUID,
    fix: LocationFixRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    validate_location_fix(fix)

    # Invariant: Row-level lock to prevent concurrent transfer acceptance race conditions
    event = db.query(Event).filter(Event.id == event_id).with_for_update().first()
    if not event or event.is_cancelled:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Event not found")

    if event.pending_host_transfer_user_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="No pending host transfer for current user",
        )

    # Invariant: Recipient must have verified fresh in-radius location fix
    recipient_pt = make_geography_point(fix.longitude, fix.latitude)
    is_inside = db.query(
        func.ST_DWithin(Event.venue_location, recipient_pt, Event.radius_meters)
    ).filter(Event.id == event.id).scalar()

    if not is_inside:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Recipient must be physically inside event radius to accept host handoff",
        )

    old_host_id = event.host_id
    event.host_id = current_user.id
    event.pending_host_transfer_user_id = None

    db.query(EventParticipant).filter(
        EventParticipant.event_id == event.id,
        EventParticipant.user_id == old_host_id,
    ).update({"role": "attendee"})

    db.query(EventParticipant).filter(
        EventParticipant.event_id == event.id,
        EventParticipant.user_id == current_user.id,
    ).update({"role": "host"})

    db.commit()
    db.refresh(event)

    return _format_event_response(event, current_user.id, recipient_pt, db)


@router.post("/{event_id}/end", response_model=EventResponse)
def end_event(
    event_id: UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    event = db.query(Event).filter(Event.id == event_id).with_for_update().first()
    if not event or event.is_cancelled:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Event not found")

    if event.host_id != current_user.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Only current host can end event")

    event.is_ended = True
    db.commit()
    db.refresh(event)

    return _format_event_response(event, current_user.id, None, db)


@router.get("/{event_id}/messages", response_model=List[ChatMessageResponse])
def get_event_messages(
    event_id: UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    event = db.query(Event).filter(Event.id == event_id).first()
    if not event or event.is_cancelled:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Event not found")

    # Invariant: Must be joined participant or host to view event chat
    is_part = db.query(EventParticipant).filter(
        EventParticipant.event_id == event_id,
        EventParticipant.user_id == current_user.id,
    ).first()
    if not is_part and event.host_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Must be an event participant to view event chat",
        )

    blocked_ids = [
        row[0] for row in db.query(UserBlock.blocked_id).filter(UserBlock.blocker_id == current_user.id).all()
    ] + [
        row[0] for row in db.query(UserBlock.blocker_id).filter(UserBlock.blocked_id == current_user.id).all()
    ]

    query = db.query(Message).filter(
        Message.channel_id == str(event_id),
        Message.channel_kind == "event",
        Message.expires_at > func.now(),
    )
    if blocked_ids:
        query = query.filter(Message.sender_id.notin_(blocked_ids))

    messages = query.order_by(Message.sequence.asc()).all()
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


@router.post("/{event_id}/messages", response_model=ChatMessageResponse, status_code=status.HTTP_201_CREATED)
def post_event_message(
    event_id: UUID,
    data: LandmarkMessageCreateRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    validate_location_fix(data.location_fix)

    event = db.query(Event).filter(Event.id == event_id).first()
    if not event or event.is_cancelled:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Event not found")

    if not event.is_started or event.is_ended:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Event chat is only active while event is running")

    is_part = db.query(EventParticipant).filter(
        EventParticipant.event_id == event_id,
        EventParticipant.user_id == current_user.id,
    ).first()
    if not is_part and event.host_id != current_user.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Must be a participant to send event messages")

    # Invariant: Sender must be physically within event venue radius
    user_pt = make_geography_point(data.location_fix.longitude, data.location_fix.latitude)
    is_inside = db.query(
        func.ST_DWithin(Event.venue_location, user_pt, Event.radius_meters)
    ).filter(Event.id == event_id).scalar()

    if not is_inside:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Outside event venue radius")

    # Ensure ChannelSequence row exists so with_for_update() locks an actual row
    db.execute(
        text(
            "INSERT INTO channel_sequences (channel_id, current_sequence) "
            "VALUES (:cid, 0) ON CONFLICT (channel_id) DO NOTHING"
        ),
        {"cid": str(event_id)},
    )

    seq_row = db.query(ChannelSequence).filter(
        ChannelSequence.channel_id == str(event_id)
    ).with_for_update().first()

    seq_row.current_sequence += 1
    next_seq = seq_row.current_sequence

    if data.attachment_id:
        media = db.query(MediaObject).filter(MediaObject.id == data.attachment_id).first()
        if not media:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Attachment media not found")
        if media.sender_id != current_user.id:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Cannot attach media uploaded by another user")
        if media.channel_kind != "event" or media.channel_id != str(event_id):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Attachment channel mismatch")
        media.sequence = next_seq

    display_name = current_user.anonymous_handle if current_user.identity_mode == "anonymous" else f"@{current_user.username}"
    is_anon = current_user.identity_mode == "anonymous"
    now_utc = datetime.now(timezone.utc)
    expires_at = now_utc + timedelta(hours=current_user.message_retention_hours)

    msg = Message(
        channel_id=str(event_id),
        channel_kind="event",
        sender_id=current_user.id,
        sender_display_name=display_name,
        is_anonymous=is_anon,
        body=data.body,
        sequence=next_seq,
        origin_location=user_pt,
        area_name=event.area_name,
        attachment_id=data.attachment_id,
        expires_at=expires_at,
    )
    db.add(msg)
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
