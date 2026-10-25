from datetime import datetime, timezone, timedelta
from uuid import uuid4
import pytest
from app.models import Landmark, LandmarkVisit, User, Event, MediaObject
from app.location import make_geography_point


def create_minimal_png():
    import struct
    return (
        b"\x89PNG\r\n\x1a\n"
        b"\x00\x00\x00\rIHDR"
        + struct.pack(">II", 32, 32)
        + b"\x08\x02\x00\x00\x00\x90wS\xde"
        b"\x00\x00\x00\x0cIDATx\x9cc\xf8\xff\xff?\x00\x05\xfe\x02\xfe\xa7V\x01\x81"
        b"\x00\x00\x00\x00IEND\xaeB`\x82"
    )


def test_future_event_cannot_be_started(client, db_session):
    # Register host
    r = client.post("/auth/register", json={
        "username": "host_future",
        "email": "host_future@example.com",
        "password": "Password123!",
    })
    token = r.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    future_start = (datetime.now(timezone.utc) + timedelta(days=5)).isoformat()
    # Create event 5 days in the future
    resp = client.post("/events", headers=headers, json={
        "title": "Future Event",
        "latitude": 40.7580,
        "longitude": -73.9855,
        "area_name": "Times Square",
        "radius_meters": 100.0,
        "starts_at": future_start,
        "access_type": "public",
        "default_open_minutes": 0,
        "default_close_minutes": 0,
    })
    assert resp.status_code == 201
    evt_id = resp.json()["id"]

    # Attempt to start event today (5 days before starts_at)
    now_iso = datetime.now(timezone.utc).isoformat()
    start_resp = client.post(
        f"/events/{evt_id}/start",
        headers=headers,
        json={
            "latitude": 40.7580,
            "longitude": -73.9855,
            "accuracy_meters": 5.0,
            "recorded_at": now_iso,
        },
    )
    # Expected: 400 Bad Request because start window hasn't opened yet
    assert start_resp.status_code == 400
    assert "earlier than 30 minutes" in start_resp.json()["detail"].lower()


def test_post_message_with_timed_out_visit_rejected(client, db_session):
    r = client.post("/auth/register", json={
        "username": "stale_sender",
        "email": "stale_sender@example.com",
        "password": "Password123!",
    })
    token = r.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    user_id = client.get("/users/me", headers=headers).json()["id"]

    lm = Landmark(
        id=uuid4(),
        name="Stale Test Landmark",
        area_name="Village",
        latitude=40.7336,
        longitude=-74.0027,
        location=make_geography_point(-74.0027, 40.7336),
        radius_meters=100.0,
    )
    db_session.add(lm)
    db_session.flush()

    # Enter landmark
    now_iso = datetime.now(timezone.utc).isoformat()
    client.post(
        f"/landmarks/{lm.id}/enter",
        headers=headers,
        json={"latitude": 40.7336, "longitude": -74.0027, "accuracy_meters": 5.0, "recorded_at": now_iso},
    )

    # Artificially age the heartbeat in DB to 5 minutes ago
    from uuid import UUID
    visit = db_session.query(LandmarkVisit).filter(
        LandmarkVisit.user_id == UUID(user_id),
        LandmarkVisit.landmark_id == lm.id,
        LandmarkVisit.is_active == True,
    ).first()
    assert visit is not None
    visit.last_heartbeat_at = datetime.now(timezone.utc) - timedelta(minutes=5)
    db_session.commit()

    # Attempt to post a message without having heartbeated/re-entered
    post_resp = client.post(
        f"/landmarks/{lm.id}/messages",
        headers=headers,
        json={
            "body": "Posting after long inactivity",
            "location_fix": {
                "latitude": 40.7336,
                "longitude": -74.0027,
                "accuracy_meters": 5.0,
                "recorded_at": datetime.now(timezone.utc).isoformat(),
            },
        },
    )
    # Expected: 403 Forbidden because visit timed out and must re-enter
    assert post_resp.status_code == 403
    assert "expired" in post_resp.json()["detail"].lower()


def test_cross_user_media_attachment_rejected(client, db_session):
    # Alice registers and uploads media
    r1 = client.post("/auth/register", json={
        "username": "alice_att",
        "email": "alice_att@example.com",
        "password": "Password123!",
    })
    alice_headers = {"Authorization": f"Bearer {r1.json()['access_token']}"}

    # Bob registers
    r2 = client.post("/auth/register", json={
        "username": "bob_att",
        "email": "bob_att@example.com",
        "password": "Password123!",
    })
    bob_headers = {"Authorization": f"Bearer {r2.json()['access_token']}"}

    lm = Landmark(
        id=uuid4(),
        name="Attachment Landmark",
        area_name="Village",
        latitude=40.7336,
        longitude=-74.0027,
        location=make_geography_point(-74.0027, 40.7336),
        radius_meters=100.0,
    )
    db_session.add(lm)
    db_session.flush()

    now_iso = datetime.now(timezone.utc).isoformat()
    # Alice enters and uploads image
    client.post(
        f"/landmarks/{lm.id}/enter",
        headers=alice_headers,
        json={"latitude": 40.7336, "longitude": -74.0027, "accuracy_meters": 5.0, "recorded_at": now_iso},
    )
    upload_resp = client.post(
        "/media/upload",
        headers=alice_headers,
        data={"channel_kind": "landmark", "channel_id": str(lm.id)},
        files={"file": ("photo.png", create_minimal_png(), "image/png")},
    )
    alice_media_id = upload_resp.json()["media_id"]

    # Bob enters landmark
    client.post(
        f"/landmarks/{lm.id}/enter",
        headers=bob_headers,
        json={"latitude": 40.7336, "longitude": -74.0027, "accuracy_meters": 5.0, "recorded_at": now_iso},
    )

    # Bob attempts to send a message hijacking Alice's media_id as attachment
    bob_post = client.post(
        f"/landmarks/{lm.id}/messages",
        headers=bob_headers,
        json={
            "body": "Bob trying to hijack Alice photo",
            "attachment_id": alice_media_id,
            "location_fix": {
                "latitude": 40.7336,
                "longitude": -74.0027,
                "accuracy_meters": 5.0,
                "recorded_at": datetime.now(timezone.utc).isoformat(),
            },
        },
    )
    # Expected: 403 Forbidden
    assert bob_post.status_code == 403
    assert "another user" in bob_post.json()["detail"].lower()


def test_dm_media_upload_safely_deferred(client, db_session):
    r = client.post("/auth/register", json={
        "username": "dm_user",
        "email": "dm_user@example.com",
        "password": "Password123!",
    })
    token = r.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    # Attempt to upload with unknown channel_id -> 404 conversation not found
    resp = client.post(
        "/media/upload",
        headers=headers,
        data={"channel_kind": "dm", "channel_id": str(uuid4())},
        files={"file": ("test.png", create_minimal_png(), "image/png")},
    )
    assert resp.status_code == 404
