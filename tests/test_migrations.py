from sqlalchemy import inspect
from app.database import test_engine, assert_not_production_db

EXPECTED_TABLES = {
    "users",
    "refresh_sessions",
    "user_blocks",
    "landmarks",
    "landmark_visits",
    "channel_sequences",
    "media_objects",
    "messages",
    "events",
    "event_participants",
}


def test_migrated_schema_has_all_tables():
    with test_engine.connect() as conn:
        assert_not_production_db(conn)
        inspector = inspect(conn)
        tables = set(inspector.get_table_names())
        for expected in EXPECTED_TABLES:
            assert expected in tables, f"Expected table {expected} not found in database"


def test_spatial_indexes_exist():
    with test_engine.connect() as conn:
        assert_not_production_db(conn)
        inspector = inspect(conn)
        landmark_indexes = {idx["name"]: idx for idx in inspector.get_indexes("landmarks")}
        assert "idx_landmarks_location" in landmark_indexes

        event_indexes = {idx["name"]: idx for idx in inspector.get_indexes("events")}
        assert "idx_events_venue_location" in event_indexes
