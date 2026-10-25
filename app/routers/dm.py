import uuid
from datetime import datetime, timezone, timedelta
from typing import List, Optional
from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session
from sqlalchemy import or_, and_, func, text

from app.database import get_db
from app.models import DmConversation, Message, User, UserBlock, MediaObject, ChannelSequence
from app.schemas import (
    DmRequestCreate,
    DmResponseRequest,
    DmMessageCreateRequest,
    DmConversationResponse,
    PublicIdentityProjection,
    ChatMessageResponse,
)
from app.dependencies import get_current_user

router = APIRouter(prefix="/dm", tags=["dm"])


def _format_dm_conversation(c: DmConversation, current_user_id: UUID, db: Session) -> DmConversationResponse:
    other_id = c.recipient_id if c.initiator_id == current_user_id else c.initiator_id
    other_user = db.query(User).filter(User.id == other_id).first()
    other_projection = PublicIdentityProjection(
        user_id=other_user.id if other_user else other_id,
        display_name=f"@{other_user.username}" if other_user else "@unknown",
        bio=other_user.bio if other_user else "",
        is_anonymous=False,
    )
    last_msg = db.query(Message).filter(
        Message.channel_id == str(c.id),
        Message.channel_kind == "dm",
        Message.expires_at > func.now(),
    ).order_by(Message.sequence.desc()).first()

    last_msg_resp = None
    if last_msg:
        last_msg_resp = ChatMessageResponse(
            id=last_msg.id,
            channel_id=last_msg.channel_id,
            sender_id=last_msg.sender_id,
            sender_display_name=last_msg.sender_display_name,
            is_anonymous=last_msg.is_anonymous,
            body=last_msg.body,
            sequence=last_msg.sequence,
            area_name="",
            distance_label="",
            attachment_id=last_msg.attachment_id,
            is_system_notice=last_msg.is_system_notice,
            created_at=last_msg.created_at,
            expires_at=last_msg.expires_at,
        )

    return DmConversationResponse(
        id=c.id,
        initiator_id=c.initiator_id,
        recipient_id=c.recipient_id,
        status=c.status,
        intro_message=c.intro_message,
        other_user=other_projection,
        is_incoming=(c.recipient_id == current_user_id),
        last_message=last_msg_resp,
        created_at=c.created_at,
        updated_at=c.updated_at,
    )


@router.post("/requests", response_model=DmConversationResponse, status_code=status.HTTP_201_CREATED)
def create_or_get_dm_request(
    data: DmRequestCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if data.recipient_id == current_user.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot message yourself",
        )

    recipient = db.query(User).filter(User.id == data.recipient_id).first()
    if not recipient:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Recipient user not found",
        )

    # Check bidirectional blocking
    is_blocked = db.query(UserBlock).filter(
        or_(
            and_(UserBlock.blocker_id == current_user.id, UserBlock.blocked_id == data.recipient_id),
            and_(UserBlock.blocker_id == data.recipient_id, UserBlock.blocked_id == current_user.id),
        )
    ).first()
    if is_blocked:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Cannot message a blocked user",
        )

    # Check existing conversation between the pair
    existing = db.query(DmConversation).filter(
        or_(
            and_(DmConversation.initiator_id == current_user.id, DmConversation.recipient_id == data.recipient_id),
            and_(DmConversation.initiator_id == data.recipient_id, DmConversation.recipient_id == current_user.id),
        )
    ).first()

    now_utc = datetime.now(timezone.utc)
    if existing:
        # If previously declined and initiator re-requests, re-open as pending
        if existing.status == "declined" and existing.initiator_id == current_user.id:
            existing.status = "pending"
            existing.intro_message = data.intro_message
            existing.updated_at = now_utc
            db.commit()
            db.refresh(existing)
        return _format_dm_conversation(existing, current_user.id, db)

    conversation = DmConversation(
        id=uuid.uuid4(),
        initiator_id=current_user.id,
        recipient_id=data.recipient_id,
        status="pending",
        intro_message=data.intro_message,
        created_at=now_utc,
        updated_at=now_utc,
    )
    db.add(conversation)
    db.commit()
    db.refresh(conversation)

    return _format_dm_conversation(conversation, current_user.id, db)


@router.get("/inbox", response_model=List[DmConversationResponse])
def get_inbox(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    blocked_ids = set(
        [r[0] for r in db.query(UserBlock.blocked_id).filter(UserBlock.blocker_id == current_user.id).all()]
        + [r[0] for r in db.query(UserBlock.blocker_id).filter(UserBlock.blocked_id == current_user.id).all()]
    )

    conversations = db.query(DmConversation).filter(
        or_(
            DmConversation.initiator_id == current_user.id,
            DmConversation.recipient_id == current_user.id,
        )
    ).order_by(DmConversation.updated_at.desc()).all()

    results = []
    for c in conversations:
        other_id = c.recipient_id if c.initiator_id == current_user.id else c.initiator_id
        if other_id in blocked_ids:
            continue
        results.append(_format_dm_conversation(c, current_user.id, db))
    return results


@router.get("/conversations/{conversation_id}", response_model=DmConversationResponse)
def get_conversation(
    conversation_id: UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    conversation = db.query(DmConversation).filter(DmConversation.id == conversation_id).first()
    if not conversation:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found")
    if current_user.id not in (conversation.initiator_id, conversation.recipient_id):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied")
    return _format_dm_conversation(conversation, current_user.id, db)


@router.post("/conversations/{conversation_id}/respond", response_model=DmConversationResponse)
def respond_to_dm(
    conversation_id: UUID,
    data: DmResponseRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    conversation = db.query(DmConversation).filter(DmConversation.id == conversation_id).first()
    if not conversation:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found")
    if conversation.recipient_id != current_user.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Only recipient can respond to request")
    if conversation.status != "pending":
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Cannot respond to conversation with status {conversation.status}")

    conversation.status = "accepted" if data.accept else "declined"
    conversation.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(conversation)
    return _format_dm_conversation(conversation, current_user.id, db)


@router.get("/conversations/{conversation_id}/messages", response_model=List[ChatMessageResponse])
def get_dm_messages(
    conversation_id: UUID,
    limit: int = Query(50, ge=1, le=100),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    conversation = db.query(DmConversation).filter(DmConversation.id == conversation_id).first()
    if not conversation:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found")
    if current_user.id not in (conversation.initiator_id, conversation.recipient_id):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied")
    if conversation.status != "accepted":
        return []

    other_id = conversation.recipient_id if conversation.initiator_id == current_user.id else conversation.initiator_id
    is_blocked = db.query(UserBlock).filter(
        or_(
            and_(UserBlock.blocker_id == current_user.id, UserBlock.blocked_id == other_id),
            and_(UserBlock.blocker_id == other_id, UserBlock.blocked_id == current_user.id),
        )
    ).first()
    if is_blocked:
        return []

    messages = db.query(Message).filter(
        Message.channel_id == str(conversation_id),
        Message.channel_kind == "dm",
        Message.expires_at > func.now(),
    ).order_by(Message.sequence.asc()).limit(limit).all()

    return [
        ChatMessageResponse(
            id=m.id,
            channel_id=m.channel_id,
            sender_id=m.sender_id,
            sender_display_name=m.sender_display_name,
            is_anonymous=m.is_anonymous,
            body=m.body,
            sequence=m.sequence,
            area_name="",
            distance_label="",
            attachment_id=m.attachment_id,
            is_system_notice=m.is_system_notice,
            created_at=m.created_at,
            expires_at=m.expires_at,
        )
        for m in messages
    ]


@router.post("/conversations/{conversation_id}/messages", response_model=ChatMessageResponse, status_code=status.HTTP_201_CREATED)
def post_dm_message(
    conversation_id: UUID,
    data: DmMessageCreateRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    conversation = db.query(DmConversation).filter(DmConversation.id == conversation_id).first()
    if not conversation:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found")
    if current_user.id not in (conversation.initiator_id, conversation.recipient_id):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied")
    if conversation.status != "accepted":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Cannot send messages until DM request is accepted",
        )

    other_id = conversation.recipient_id if conversation.initiator_id == current_user.id else conversation.initiator_id
    is_blocked = db.query(UserBlock).filter(
        or_(
            and_(UserBlock.blocker_id == current_user.id, UserBlock.blocked_id == other_id),
            and_(UserBlock.blocker_id == other_id, UserBlock.blocked_id == current_user.id),
        )
    ).first()
    if is_blocked:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Blocked participant")

    db.execute(
        text(
            "INSERT INTO channel_sequences (channel_id, current_sequence) "
            "VALUES (:cid, 0) ON CONFLICT (channel_id) DO NOTHING"
        ),
        {"cid": str(conversation_id)},
    )

    seq_row = db.query(ChannelSequence).filter(
        ChannelSequence.channel_id == str(conversation_id)
    ).with_for_update().first()

    seq_row.current_sequence += 1
    next_seq = seq_row.current_sequence

    if data.attachment_id:
        media = db.query(MediaObject).filter(MediaObject.id == data.attachment_id).first()
        if not media:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Attachment media not found")
        if media.sender_id != current_user.id:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Cannot attach media uploaded by another user")
        if media.channel_kind != "dm" or media.channel_id != str(conversation_id):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Attachment channel mismatch")
        media.sequence = next_seq

    now_utc = datetime.now(timezone.utc)
    expires_at = now_utc + timedelta(hours=current_user.message_retention_hours)
    display_name = f"@{current_user.username}"

    msg = Message(
        channel_id=str(conversation_id),
        channel_kind="dm",
        sender_id=current_user.id,
        sender_display_name=display_name,
        is_anonymous=False,
        body=data.body,
        sequence=next_seq,
        origin_location=None,
        area_name="",
        attachment_id=data.attachment_id,
        expires_at=expires_at,
    )
    db.add(msg)
    conversation.updated_at = now_utc
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
        area_name="",
        distance_label="",
        attachment_id=msg.attachment_id,
        is_system_notice=msg.is_system_notice,
        created_at=msg.created_at,
        expires_at=msg.expires_at,
    )
