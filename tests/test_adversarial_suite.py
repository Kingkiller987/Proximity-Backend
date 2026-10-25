from datetime import datetime, timezone, timedelta
from uuid import uuid4
import pytest
from app.models import Landmark, LandmarkVisit, User, Event, EventParticipant, Message
from app.location import make_geography_point
from app.schedule import is_within_operating_window


def create_minimal_png():
    import struct
    return (
        b"\x89PNG\r\n\x1a\n"
        b"\x00\x00\x00\rIHDR"
        + struct.pack(">II", 16, 16)
        + b"\x08\x02\x00\x00\x00\x90wS\xde"
        b"\x00\x00\x00\x0cIDATx\x9cc\xf8\xff\xff?\x00\x05\xfe\x02\xfe\xa7V\x01\x81"
        b"\x00\x00\x00\x00IEND\xaeB`\x82"
    )


def test_location_validation_edge_cases(client, db_session):
    r = client.post("/auth/register", json={
        "username": "loc_tester",
        "email": "loc_tester@example.com",
        "password": "Password123!",
    })
    headers = {"Authorization": f"Bearer {r.json()['access_token']}"}

    lm = Landmark(
        id=uuid4(),
        name="Loc Landmark",
        area_name="Village",
        latitude=40.7336,
        longitude=-74.0027,
        location=make_geography_point(-74.0027, 40.7336),
        radius_meters=100.0,
    )
    db_session.add(lm)
    db_session.flush()

    # 1. Inaccuracy > 50m rejected
    resp_inaccurate = client.post(
        f"/landmarks/{lm.id}/enter",
        headers=headers,
        json={"latitude": 40.7336, "longitude": -74.0027, "accuracy_meters": 55.0, "recorded_at": datetime.now(timezone.utc).isoformat()},
    )
    assert resp_inaccurate.status_code == 400
    assert "accuracy" in resp_inaccurate.json()["detail"].lower()

    # 2. Stale timestamp (> 30s) rejected
    stale_iso = (datetime.now(timezone.utc) - timedelta(seconds=45)).isoformat()
    resp_stale = client.post(
        f"/landmarks/{lm.id}/enter",
        headers=headers,
        json={"latitude": 40.7336, "longitude": -74.0027, "accuracy_meters": 10.0, "recorded_at": stale_iso},
    )
    assert resp_stale.status_code == 400
    assert "stale" in resp_stale.json()["detail"].lower()

    # 3. Future timestamp (> 5s) rejected
    future_iso = (datetime.now(timezone.utc) + timedelta(seconds=20)).isoformat()
    resp_future = client.post(
        f"/landmarks/{lm.id}/enter",
        headers=headers,
        json={"latitude": 40.7336, "longitude": -74.0027, "accuracy_meters": 10.0, "recorded_at": future_iso},
    )
    assert resp_future.status_code == 400
    assert "future" in resp_future.json()["detail"].lower()

    # 4. Valid location fix accepted
    resp_ok = client.post(
        f"/landmarks/{lm.id}/enter",
        headers=headers,
        json={"latitude": 40.7336, "longitude": -74.0027, "accuracy_meters": 5.0, "recorded_at": datetime.now(timezone.utc).isoformat()},
    )
    assert resp_ok.status_code == 200


def test_pagination_cursor_cannot_bypass_visit_isolation(client, db_session):
    # Setup sender
    r_sender = client.post("/auth/register", json={
        "username": "prior_sender",
        "email": "prior_sender@example.com",
        "password": "Password123!",
    })
    sender_headers = {"Authorization": f"Bearer {r_sender.json()['access_token']}"}

    lm = Landmark(
        id=uuid4(),
        name="Pagination Landmark",
        area_name="Village",
        latitude=40.7336,
        longitude=-74.0027,
        location=make_geography_point(-74.0027, 40.7336),
        radius_meters=100.0,
    )
    db_session.add(lm)
    db_session.flush()

    # Sender enters and posts 3 messages
    now_iso = datetime.now(timezone.utc).isoformat()
    client.post(
        f"/landmarks/{lm.id}/enter",
        headers=sender_headers,
        json={"latitude": 40.7336, "longitude": -74.0027, "accuracy_meters": 5.0, "recorded_at": now_iso},
    )
    for i in range(1, 4):
        p_res = client.post(
            f"/landmarks/{lm.id}/messages",
            headers=sender_headers,
            json={
                "body": f"Prior message {i}",
                "location_fix": {"latitude": 40.7336, "longitude": -74.0027, "accuracy_meters": 5.0, "recorded_at": datetime.now(timezone.utc).isoformat()},
            },
        )
        assert p_res.status_code == 201

    # New user registers and enters landmark (min_sequence will be 4)
    r_new = client.post("/auth/register", json={
        "username": "new_visitor",
        "email": "new_visitor@example.com",
        "password": "Password123!",
    })
    new_headers = {"Authorization": f"Bearer {r_new.json()['access_token']}"}
    enter_res = client.post(
        f"/landmarks/{lm.id}/enter",
        headers=new_headers,
        json={"latitude": 40.7336, "longitude": -74.0027, "accuracy_meters": 5.0, "recorded_at": datetime.now(timezone.utc).isoformat()},
    )
    assert enter_res.status_code == 200
    assert enter_res.json()["min_message_sequence"] == 4

    # New user attempts to query with old cursor since_sequence=0
    msgs_cursor0 = client.get(
        f"/landmarks/{lm.id}/messages",
        headers=new_headers,
        params={"since_sequence": 0},
    )
    assert msgs_cursor0.status_code == 200
    assert len(msgs_cursor0.json()) == 0  # Messages 1..3 strictly inaccessible!

    # Sender posts message 4
    client.post(
        f"/landmarks/{lm.id}/messages",
        headers=sender_headers,
        json={
            "body": "Message 4 after visitor entry",
            "location_fix": {"latitude": 40.7336, "longitude": -74.0027, "accuracy_meters": 5.0, "recorded_at": datetime.now(timezone.utc).isoformat()},
        },
    )

    # Visitor queries now: message 4 is visible
    msgs_now = client.get(
        f"/landmarks/{lm.id}/messages",
        headers=new_headers,
        params={"since_sequence": 0},
    )
    assert msgs_now.status_code == 200
    assert len(msgs_now.json()) == 1
    assert msgs_now.json()[0]["body"] == "Message 4 after visitor entry"


def test_tampered_media_token_rejected(client, db_session):
    # Register user
    r = client.post("/auth/register", json={
        "username": "tamper_user",
        "email": "tamper_user@example.com",
        "password": "Password123!",
    })
    headers = {"Authorization": f"Bearer {r.json()['access_token']}"}

    # Attempt to download non-existent media
    random_id = uuid4()
    resp_404 = client.get(f"/media/{random_id}", headers=headers)
    assert resp_404.status_code == 404

    # Tampered token string
    tampered_token = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.e30.tampered"
    resp_tamper = client.get(f"/media/{random_id}?token={tampered_token}")
    assert resp_tamper.status_code == 403


def test_event_messaging_and_authorization(client, db_session):
    # Host and attendee
    r_h = client.post("/auth/register", json={
        "username": "evt_chat_host",
        "email": "evt_chat_host@example.com",
        "password": "Password123!",
    })
    host_headers = {"Authorization": f"Bearer {r_h.json()['access_token']}"}

    r_att = client.post("/auth/register", json={
        "username": "evt_chat_att",
        "email": "evt_chat_att@example.com",
        "password": "Password123!",
    })
    att_headers = {"Authorization": f"Bearer {r_att.json()['access_token']}"}

    r_outsider = client.post("/auth/register", json={
        "username": "evt_outsider",
        "email": "evt_outsider@example.com",
        "password": "Password123!",
    })
    outsider_headers = {"Authorization": f"Bearer {r_outsider.json()['access_token']}"}

    now_utc = datetime.now(timezone.utc)
    # Create event starting now
    evt_resp = client.post("/events", headers=host_headers, json={
        "title": "Chat Event",
        "latitude": 40.7580,
        "longitude": -73.9855,
        "area_name": "Times Square",
        "radius_meters": 150.0,
        "starts_at": now_utc.isoformat(),
        "access_type": "public",
        "default_open_minutes": 0,
        "default_close_minutes": 0,
    })
    assert evt_resp.status_code == 201
    evt_id = evt_resp.json()["id"]

    # Attendee joins event
    client.post(f"/events/{evt_id}/join", headers=att_headers, json={})

    # Host starts event
    now_iso = datetime.now(timezone.utc).isoformat()
    client.post(
        f"/events/{evt_id}/start",
        headers=host_headers,
        json={"latitude": 40.7580, "longitude": -73.9855, "accuracy_meters": 5.0, "recorded_at": now_iso},
    )

    # Outsider attempts to post message -> 403 Forbidden
    out_post = client.post(
        f"/events/{evt_id}/messages",
        headers=outsider_headers,
        json={
            "body": "Uninvited message",
            "location_fix": {"latitude": 40.7580, "longitude": -73.9855, "accuracy_meters": 5.0, "recorded_at": now_iso},
        },
    )
    assert out_post.status_code == 403

    # Attendee sends message inside radius -> 201 Created
    att_post = client.post(
        f"/events/{evt_id}/messages",
        headers=att_headers,
        json={
            "body": "Hello from attendee in venue!",
            "location_fix": {"latitude": 40.7580, "longitude": -73.9855, "accuracy_meters": 5.0, "recorded_at": now_iso},
        },
    )
    assert att_post.status_code == 201
    assert att_post.json()["sequence"] == 1

    # Host reads event messages
    host_msgs = client.get(f"/events/{evt_id}/messages", headers=host_headers)
    assert host_msgs.status_code == 200
    assert len(host_msgs.json()) == 1
    assert host_msgs.json()[0]["body"] == "Hello from attendee in venue!"

    # Outsider attempts to read event messages -> 403 Forbidden
    out_get = client.get(f"/events/{evt_id}/messages", headers=outsider_headers)
    assert out_get.status_code == 403


def test_dst_transition_operating_window():
    # Venue in New York (America/New_York)
    # On Sunday, Nov 1, 2026, 2:00 AM EDT clocks turn backward to 1:00 AM EST (Fall back)
    evt = Event(
        venue_timezone="America/New_York",
        default_open_minutes=600,   # 10:00 AM
        default_close_minutes=1200, # 8:00 PM
        daily_overrides={},
    )

    # In Summer/Early Fall (EDT: UTC-4): 15:00 UTC = 11:00 AM EDT (Open)
    dt_summer = datetime(2026, 8, 1, 15, 0, tzinfo=timezone.utc)
    assert is_within_operating_window(evt, dt_summer) is True

    # After Fall back (EST: UTC-5): 15:00 UTC = 10:00 AM EST (Open)
    dt_winter = datetime(2026, 12, 1, 15, 0, tzinfo=timezone.utc)
    assert is_within_operating_window(evt, dt_winter) is True

    # 14:00 UTC in winter is 09:00 AM EST (Closed, before 10:00 AM)
    dt_winter_early = datetime(2026, 12, 1, 14, 0, tzinfo=timezone.utc)
    assert is_within_operating_window(evt, dt_winter_early) is False
