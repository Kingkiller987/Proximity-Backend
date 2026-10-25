from datetime import datetime
from typing import Optional, List, Dict, Any
from uuid import UUID
from pydantic import BaseModel, Field, EmailStr, field_validator


# --- AUTH & USER ---

class UserRegisterRequest(BaseModel):
    username: str = Field(..., min_length=3, max_length=20, pattern=r"^[a-zA-Z0-9_]+$")
    email: str = Field(..., pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
    password: str = Field(..., min_length=8)


class UserLoginRequest(BaseModel):
    username: str
    password: str


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int


class RefreshTokenRequest(BaseModel):
    refresh_token: str


class UserProfileResponse(BaseModel):
    id: UUID
    username: str
    email: str
    anonymous_handle: str
    identity_mode: str
    visible_in_nearby: bool
    ghost_browse_nearby: bool
    is_ghost_mode: bool
    nearby_radius_meters: float
    message_retention_hours: int
    bio: str
    created_at: datetime

    class Config:
        from_attributes = True


class PublicIdentityProjection(BaseModel):
    user_id: UUID
    display_name: str
    bio: str
    is_anonymous: bool


class UserUpdateRequest(BaseModel):
    bio: Optional[str] = None
    identity_mode: Optional[str] = Field(None, pattern=r"^(anonymous|username)$")
    visible_in_nearby: Optional[bool] = None
    nearby_radius_meters: Optional[float] = Field(None, ge=50.0, le=5000.0)


# --- LOCATION ---

class LocationFixRequest(BaseModel):
    latitude: float = Field(..., ge=-90.0, le=90.0)
    longitude: float = Field(..., ge=-180.0, le=180.0)
    accuracy_meters: float = Field(..., ge=0.0)
    recorded_at: datetime
    area_name: Optional[str] = None


class GeoPointResponse(BaseModel):
    latitude: float
    longitude: float
    area_name: str
    timezone: str = "America/New_York"


# --- LANDMARKS ---

class LandmarkResponse(BaseModel):
    id: UUID
    name: str
    area_name: str
    latitude: float
    longitude: float
    radius_meters: float
    category: str
    distance_meters: Optional[float] = None
    distance_label: Optional[str] = None
    is_user_inside: Optional[bool] = None

    class Config:
        from_attributes = True


class LandmarkVisitResponse(BaseModel):
    session_id: UUID
    landmark_id: UUID
    entered_at: datetime
    last_heartbeat_at: datetime
    min_message_sequence: int
    is_active: bool


class LandmarkMessageCreateRequest(BaseModel):
    body: str = Field(..., min_length=1, max_length=2000)
    attachment_id: Optional[UUID] = None
    location_fix: LocationFixRequest


class ChatMessageResponse(BaseModel):
    id: UUID
    channel_id: str
    sender_id: UUID
    sender_display_name: str
    is_anonymous: bool
    body: str
    sequence: int
    area_name: str
    distance_label: str
    attachment_id: Optional[UUID] = None
    is_system_notice: bool
    created_at: datetime
    expires_at: datetime


# --- MEDIA ---

class MediaUploadResponse(BaseModel):
    media_id: UUID
    mime_type: str
    size_bytes: int
    width: int
    height: int


class MediaDownloadTokenResponse(BaseModel):
    token: str
    media_id: UUID
    channel_id: str
    expires_at: datetime


# --- EVENTS ---

class DailyScheduleOverride(BaseModel):
    is_closed: bool = False
    open_minutes: Optional[int] = Field(None, ge=0, lt=1440)
    close_minutes: Optional[int] = Field(None, ge=0, lt=1440)


class EventCreateRequest(BaseModel):
    title: str = Field(..., min_length=2, max_length=100)
    description: str = Field("", max_length=5000)
    category_tag: str = "social"
    latitude: float = Field(..., ge=-90.0, le=90.0)
    longitude: float = Field(..., ge=-180.0, le=180.0)
    area_name: str
    radius_meters: float = Field(100.0, ge=10.0, le=2000.0)
    access_type: str = Field("public", pattern=r"^(public|joinCode|inviteOnly)$")
    join_code: Optional[str] = Field(None, min_length=4, max_length=32)
    starts_at: datetime
    duration_seconds: int = Field(7200, ge=300)
    venue_timezone: str = "America/New_York"
    default_open_minutes: int = Field(540, ge=0, lt=1440)
    default_close_minutes: int = Field(1320, ge=0, lt=1440)
    daily_overrides: Dict[str, DailyScheduleOverride] = Field(default_factory=dict)


class EventResponse(BaseModel):
    id: UUID
    host_id: UUID
    title: str
    description: str
    category_tag: str
    area_name: str
    radius_meters: float
    access_type: str
    starts_at: datetime
    expires_at: datetime
    venue_timezone: str
    default_open_minutes: int
    default_close_minutes: int
    daily_overrides: Dict[str, Any]
    is_cancelled: bool
    is_ended: bool
    is_started: bool
    started_at: Optional[datetime] = None
    participant_count: Optional[int] = None
    is_joined: Optional[bool] = None
    is_host: Optional[bool] = None
    distance_label: Optional[str] = None
    created_at: datetime

    class Config:
        from_attributes = True


class EventJoinRequest(BaseModel):
    join_code: Optional[str] = None


class EventTransferRequest(BaseModel):
    recipient_user_id: UUID


# --- NEARBY ---

class NearbyParticipantResponse(BaseModel):
    user_id: UUID
    display_name: str
    is_anonymous: bool
    area_name: str
    distance_label: str
    visible_in_nearby: bool = True
