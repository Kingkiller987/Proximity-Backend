"""initial_schema

Revision ID: b2f45d002849
Revises: 
Create Date: 2026-10-25 07:22:00.000000

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from app.models import GeographyPoint

revision: str = "b2f45d002849"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. users
    op.create_table(
        "users",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("username", sa.String(length=30), nullable=False),
        sa.Column("email", sa.String(length=255), nullable=False),
        sa.Column("password_hash", sa.String(length=255), nullable=False),
        sa.Column("anonymous_handle", sa.String(length=50), nullable=False),
        sa.Column("identity_mode", sa.String(length=20), server_default="username", nullable=False),
        sa.Column("visible_in_nearby", sa.Boolean(), server_default="true", nullable=False),
        sa.Column("ghost_browse_nearby", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("is_ghost_mode", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("nearby_radius_meters", sa.Float(), server_default="500.0", nullable=False),
        sa.Column("message_retention_hours", sa.Integer(), server_default="24", nullable=False),
        sa.Column("bio", sa.Text(), server_default="", nullable=False),
        sa.Column("avatar_storage_key", sa.String(length=255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    op.create_index("ix_users_username", "users", ["username"], unique=True)
    op.create_index("ix_users_email", "users", ["email"], unique=True)

    # 2. refresh_sessions
    op.create_table(
        "refresh_sessions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("device_info", sa.String(length=255), server_default="", nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    op.create_index("ix_refresh_sessions_user_id", "refresh_sessions", ["user_id"])
    op.create_index("ix_refresh_sessions_token_hash", "refresh_sessions", ["token_hash"], unique=True)

    # 3. user_blocks
    op.create_table(
        "user_blocks",
        sa.Column("blocker_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("blocked_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )

    # 4. landmarks
    op.create_table(
        "landmarks",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("area_name", sa.String(length=100), nullable=False),
        sa.Column("latitude", sa.Float(), nullable=False),
        sa.Column("longitude", sa.Float(), nullable=False),
        sa.Column("location", GeographyPoint(), nullable=False),
        sa.Column("radius_meters", sa.Float(), server_default="100.0", nullable=False),
        sa.Column("category", sa.String(length=50), server_default="pointOfInterest", nullable=False),
        sa.Column("parent_landmark_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("landmarks.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    op.create_index("idx_landmarks_location", "landmarks", ["location"], postgresql_using="gist")

    # 5. landmark_visits
    op.create_table(
        "landmark_visits",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("landmark_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("landmarks.id", ondelete="CASCADE"), nullable=False),
        sa.Column("entered_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("last_heartbeat_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("min_message_sequence", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default="true", nullable=False),
        sa.Column("exit_reason", sa.String(length=50), nullable=True),
    )
    op.create_index("idx_landmark_visits_active", "landmark_visits", ["user_id", "landmark_id", "is_active"])

    # 6. channel_sequences
    op.create_table(
        "channel_sequences",
        sa.Column("channel_id", sa.String(length=100), primary_key=True),
        sa.Column("current_sequence", sa.BigInteger(), server_default="0", nullable=False),
    )

    # 7. media_objects
    op.create_table(
        "media_objects",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("storage_path", sa.String(length=255), nullable=False),
        sa.Column("channel_kind", sa.String(length=20), nullable=False),
        sa.Column("channel_id", sa.String(length=100), nullable=False),
        sa.Column("sender_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("mime_type", sa.String(length=50), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("width", sa.Integer(), server_default="0", nullable=False),
        sa.Column("height", sa.Integer(), server_default="0", nullable=False),
        sa.Column("sequence", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_media_objects_channel_id", "media_objects", ["channel_id"])
    op.create_index("ix_media_objects_expires_at", "media_objects", ["expires_at"])

    # 8. messages
    op.create_table(
        "messages",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("channel_id", sa.String(length=100), nullable=False),
        sa.Column("channel_kind", sa.String(length=20), nullable=False),
        sa.Column("sender_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("sender_display_name", sa.String(length=100), nullable=False),
        sa.Column("is_anonymous", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("sequence", sa.BigInteger(), nullable=False),
        sa.Column("origin_location", GeographyPoint(), nullable=True),
        sa.Column("area_name", sa.String(length=100), server_default="", nullable=False),
        sa.Column("attachment_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("media_objects.id", ondelete="SET NULL"), nullable=True),
        sa.Column("is_system_notice", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("idx_messages_channel_seq", "messages", ["channel_id", "sequence", "created_at"])

    # 9. events
    op.create_table(
        "events",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("host_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("title", sa.String(length=100), nullable=False),
        sa.Column("description", sa.Text(), server_default="", nullable=False),
        sa.Column("category_tag", sa.String(length=50), server_default="social", nullable=False),
        sa.Column("venue_location", GeographyPoint(), nullable=False),
        sa.Column("area_name", sa.String(length=100), nullable=False),
        sa.Column("radius_meters", sa.Float(), server_default="100.0", nullable=False),
        sa.Column("access_type", sa.String(length=20), server_default="public", nullable=False),
        sa.Column("join_code_hash", sa.String(length=128), nullable=True),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("duration_seconds", sa.Integer(), server_default="7200", nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("venue_timezone", sa.String(length=50), server_default="America/New_York", nullable=False),
        sa.Column("default_open_minutes", sa.Integer(), server_default="540", nullable=False),
        sa.Column("default_close_minutes", sa.Integer(), server_default="1320", nullable=False),
        sa.Column("daily_overrides", sa.JSON(), server_default="{}", nullable=False),
        sa.Column("is_cancelled", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("is_ended", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("is_started", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("pending_host_transfer_user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("location_move_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("last_moved_notice", sa.Text(), nullable=True),
        sa.Column("poster_media_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("media_objects.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    op.create_index("idx_events_venue_location", "events", ["venue_location"], postgresql_using="gist")

    # 10. event_participants
    op.create_table(
        "event_participants",
        sa.Column("event_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("events.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("role", sa.String(length=20), server_default="attendee", nullable=False),
        sa.Column("joined_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("event_participants")
    op.drop_table("events")
    op.drop_table("messages")
    op.drop_table("media_objects")
    op.drop_table("channel_sequences")
    op.drop_table("landmark_visits")
    op.drop_table("landmarks")
    op.drop_table("user_blocks")
    op.drop_table("refresh_sessions")
    op.drop_table("users")
