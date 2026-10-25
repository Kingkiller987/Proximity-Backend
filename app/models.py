import uuid
from datetime import datetime
from sqlalchemy import (
    Column,
    String,
    Boolean,
    Float,
    Integer,
    BigInteger,
    Text,
    DateTime,
    ForeignKey,
    Index,
    JSON,
    func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import DeclarativeBase, relationship
from sqlalchemy.types import UserDefinedType


class Base(DeclarativeBase):
    pass


class GeographyPoint(UserDefinedType):
    """Custom PostgreSQL PostGIS geography(Point, 4326) type."""
    def get_col_spec(self, **kw):
        return "geography(Point, 4326)"


class User(Base):
    __tablename__ = "users"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    username = Column(String(30), unique=True, index=True, nullable=False)
    email = Column(String(255), unique=True, index=True, nullable=False)
    password_hash = Column(String(255), nullable=False)
    anonymous_handle = Column(String(50), nullable=False)
    identity_mode = Column(String(20), default="username", nullable=False)  # anonymous | username
    visible_in_nearby = Column(Boolean, default=True, nullable=False)
    ghost_browse_nearby = Column(Boolean, default=False, nullable=False)
    is_ghost_mode = Column(Boolean, default=False, nullable=False)
    nearby_radius_meters = Column(Float, default=500.0, nullable=False)
    message_retention_hours = Column(Integer, default=24, nullable=False)
    bio = Column(Text, default="", nullable=False)
    avatar_storage_key = Column(String(255), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    refresh_sessions = relationship("RefreshSession", back_populates="user", cascade="all, delete-orphan")
    visits = relationship("LandmarkVisit", back_populates="user", cascade="all, delete-orphan")


class RefreshSession(Base):
    __tablename__ = "refresh_sessions"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False)
    token_hash = Column(String(64), unique=True, index=True, nullable=False)
    device_info = Column(String(255), default="", nullable=False)
    expires_at = Column(DateTime(timezone=True), nullable=False)
    revoked_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    user = relationship("User", back_populates="refresh_sessions")


class UserBlock(Base):
    __tablename__ = "user_blocks"

    blocker_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    blocked_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class Landmark(Base):
    __tablename__ = "landmarks"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(String(100), nullable=False)
    area_name = Column(String(100), nullable=False)
    latitude = Column(Float, nullable=False)
    longitude = Column(Float, nullable=False)
    location = Column(GeographyPoint, nullable=False)
    radius_meters = Column(Float, default=100.0, nullable=False)
    category = Column(String(50), default="pointOfInterest", nullable=False)
    parent_landmark_id = Column(UUID(as_uuid=True), ForeignKey("landmarks.id", ondelete="SET NULL"), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    visits = relationship("LandmarkVisit", back_populates="landmark", cascade="all, delete-orphan")

    __table_args__ = (
        Index("idx_landmarks_location", "location", postgresql_using="gist"),
    )


class LandmarkVisit(Base):
    __tablename__ = "landmark_visits"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False)
    landmark_id = Column(UUID(as_uuid=True), ForeignKey("landmarks.id", ondelete="CASCADE"), index=True, nullable=False)
    entered_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    last_heartbeat_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    min_message_sequence = Column(BigInteger, default=0, nullable=False)
    is_active = Column(Boolean, default=True, index=True, nullable=False)
    exit_reason = Column(String(50), nullable=True)

    user = relationship("User", back_populates="visits")
    landmark = relationship("Landmark", back_populates="visits")

    __table_args__ = (
        Index("idx_landmark_visits_active", "user_id", "landmark_id", "is_active"),
    )


class ChannelSequence(Base):
    __tablename__ = "channel_sequences"

    channel_id = Column(String(100), primary_key=True)
    current_sequence = Column(BigInteger, default=0, nullable=False)


class MediaObject(Base):
    __tablename__ = "media_objects"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    storage_path = Column(String(255), nullable=False)
    channel_kind = Column(String(20), nullable=False)  # landmark | event | dm
    channel_id = Column(String(100), index=True, nullable=False)
    sender_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False)
    mime_type = Column(String(50), nullable=False)
    size_bytes = Column(Integer, nullable=False)
    width = Column(Integer, default=0, nullable=False)
    height = Column(Integer, default=0, nullable=False)
    sequence = Column(BigInteger, default=0, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    expires_at = Column(DateTime(timezone=True), index=True, nullable=False)


class Message(Base):
    __tablename__ = "messages"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    channel_id = Column(String(100), index=True, nullable=False)
    channel_kind = Column(String(20), nullable=False)  # landmark | event | dm
    sender_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False)
    sender_display_name = Column(String(100), nullable=False)
    is_anonymous = Column(Boolean, default=False, nullable=False)
    body = Column(Text, nullable=False)
    sequence = Column(BigInteger, index=True, nullable=False)
    origin_location = Column(GeographyPoint, nullable=True)  # Strictly server-side only
    area_name = Column(String(100), default="", nullable=False)
    attachment_id = Column(UUID(as_uuid=True), ForeignKey("media_objects.id", ondelete="SET NULL"), nullable=True)
    is_system_notice = Column(Boolean, default=False, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), index=True, nullable=False)
    expires_at = Column(DateTime(timezone=True), index=True, nullable=False)

    attachment = relationship("MediaObject")

    __table_args__ = (
        Index("idx_messages_channel_seq", "channel_id", "sequence", "created_at"),
    )


class Event(Base):
    __tablename__ = "events"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    host_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False)
    title = Column(String(100), nullable=False)
    description = Column(Text, default="", nullable=False)
    category_tag = Column(String(50), default="social", nullable=False)
    venue_location = Column(GeographyPoint, nullable=False)
    area_name = Column(String(100), nullable=False)
    radius_meters = Column(Float, default=100.0, nullable=False)
    access_type = Column(String(20), default="public", nullable=False)  # public | joinCode | inviteOnly
    join_code_hash = Column(String(128), nullable=True)
    starts_at = Column(DateTime(timezone=True), index=True, nullable=False)
    duration_seconds = Column(Integer, default=7200, nullable=False)
    expires_at = Column(DateTime(timezone=True), index=True, nullable=False)
    venue_timezone = Column(String(50), default="America/New_York", nullable=False)
    default_open_minutes = Column(Integer, default=540, nullable=False)
    default_close_minutes = Column(Integer, default=1320, nullable=False)
    daily_overrides = Column(JSON, default=dict, nullable=False)
    is_cancelled = Column(Boolean, default=False, nullable=False)
    is_ended = Column(Boolean, default=False, nullable=False)
    is_started = Column(Boolean, default=False, nullable=False)
    started_at = Column(DateTime(timezone=True), nullable=True)
    pending_host_transfer_user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    location_move_count = Column(Integer, default=0, nullable=False)
    last_moved_notice = Column(Text, nullable=True)
    poster_media_id = Column(UUID(as_uuid=True), ForeignKey("media_objects.id", ondelete="SET NULL"), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    participants = relationship("EventParticipant", back_populates="event", cascade="all, delete-orphan")

    __table_args__ = (
        Index("idx_events_venue_location", "venue_location", postgresql_using="gist"),
    )


class EventParticipant(Base):
    __tablename__ = "event_participants"

    event_id = Column(UUID(as_uuid=True), ForeignKey("events.id", ondelete="CASCADE"), primary_key=True)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    role = Column(String(20), default="attendee", nullable=False)  # host | attendee
    joined_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    event = relationship("Event", back_populates="participants")
