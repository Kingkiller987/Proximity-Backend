from datetime import datetime, timezone, timedelta
from uuid import uuid4
import pytest
from app.models import Event, EventParticipant, User
from app.schedule import is_within_operating_window


def test_operating_window_daytime_and_overnight():
    evt_day = Event(
        venue_timezone="America/New_York",
        default_open_minutes=540,   # 09:00
        default_close_minutes=1020, # 17:00
        daily_overrides={},
    )
    # 12:00 UTC = 08:00 EDT (closed before 09:00)
    dt_early = datetime(2026, 10, 25, 12, 0, tzinfo=timezone.utc)
    assert not is_within_operating_window(evt_day, dt_early)

    # 16:00 UTC = 12:00 EDT (open)
    dt_mid = datetime(2026, 10, 25, 16, 0, tzinfo=timezone.utc)
    assert is_within_operating_window(evt_day, dt_mid)

    # Overnight event: 20:00 (1200) to 02:00 (120)
    evt_night = Event(
        venue_timezone="America/New_York",
        default_open_minutes=1200,
        default_close_minutes=120,
        daily_overrides={},
    )
    # 01:00 EDT (curr_minutes = 60 < 120 -> open)
    # In October EDT (UTC-4), 05:00 UTC is 01:00 EDT
    dt_night_open = datetime(2026, 10, 25, 5, 0, tzinfo=timezone.utc)
    assert is_within_operating_window(evt_night, dt_night_open)

    # 12:00 EDT (curr_minutes = 720 -> closed)
    # 16:00 UTC is 12:00 EDT
    dt_night_closed = datetime(2026, 10, 25, 16, 0, tzinfo=timezone.utc)
    assert not is_within_operating_window(evt_night, dt_night_closed)


def test_event_lifecycle_and_two_phase_handoff(client, db_session):
    # 1. Register host and attendee
    r_host = client.post("/auth/register", json={
        "username": "event_host",
        "email": "host@example.com",
        "password": "Password123!",
    })
    host_token = r_host.json()["access_token"]
    host_headers = {"Authorization": f"Bearer {host_token}"}
    host_id = client.get("/users/me", headers=host_headers).json()["id"]

    r_attendee = client.post("/auth/register", json={
        "username": "event_attendee",
        "email": "attendee@example.com",
        "password": "Password123!",
    })
    attendee_token = r_attendee.json()["access_token"]
    attendee_headers = {"Authorization": f"Bearer {attendee_token}"}
    attendee_id = client.get("/users/me", headers=attendee_headers).json()["id"]

    # 2. Reject event creation with past start date
    past_iso = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
    resp_past = client.post("/events", headers=host_headers, json={
        "title": "Past Event",
        "latitude": 40.7580,
        "longitude": -73.9855,
        "area_name": "Times Square",
        "radius_meters": 150.0,
        "starts_at": past_iso,
        "access_type": "public",
    })
    assert resp_past.status_code == 400
    assert "cannot be in the past" in resp_past.json()["detail"].lower()

    # 3. Create valid joinCode event with 24-hr open window
    future_start = (datetime.now(timezone.utc) + timedelta(minutes=10)).isoformat()
    resp_create = client.post("/events", headers=host_headers, json={
        "title": "Secret Tech Meetup",
        "description": "Exclusive developer gathering inside Times Square",
        "latitude": 40.7580,
        "longitude": -73.9855,
        "area_name": "Times Square",
        "radius_meters": 150.0,
        "access_type": "joinCode",
        "join_code": "secret123",
        "starts_at": future_start,
        "duration_seconds": 7200,
        "venue_timezone": "America/New_York",
        "default_open_minutes": 0,
        "default_close_minutes": 0,
    })
    assert resp_create.status_code == 201
    evt_data = resp_create.json()
    evt_id = evt_data["id"]
    assert evt_data["is_host"] is True

    # 4. Discover events as non-joined attendee: description should be redacted
    resp_list = client.get(
        "/events",
        headers=attendee_headers,
        params={"latitude": 40.7580, "longitude": -73.9855},
    )
    assert resp_list.status_code == 200
    found = [e for e in resp_list.json() if e["id"] == evt_id][0]
    assert found["description"] == ""  # Redacted
    assert found["is_joined"] is False

    # 5. Attendee joins with invalid code -> 403
    resp_bad_join = client.post(
        f"/events/{evt_id}/join",
        headers=attendee_headers,
        json={"join_code": "wrong_code"},
    )
    assert resp_bad_join.status_code == 403

    # Attendee joins with correct code -> 200
    resp_good_join = client.post(
        f"/events/{evt_id}/join",
        headers=attendee_headers,
        json={"join_code": "secret123"},
    )
    assert resp_good_join.status_code == 200

    # 6. Host starts event outside radius -> 403
    now_iso = datetime.now(timezone.utc).isoformat()
    resp_outside_start = client.post(
        f"/events/{evt_id}/start",
        headers=host_headers,
        json={
            "latitude": 40.7000,
            "longitude": -74.0000,
            "accuracy_meters": 10.0,
            "recorded_at": now_iso,
        },
    )
    assert resp_outside_start.status_code == 403
    assert "inside event radius" in resp_outside_start.json()["detail"].lower()

    # Host starts event inside radius -> 200 OK
    resp_start = client.post(
        f"/events/{evt_id}/start",
        headers=host_headers,
        json={
            "latitude": 40.7580,
            "longitude": -73.9855,
            "accuracy_meters": 10.0,
            "recorded_at": now_iso,
        },
    )
    assert resp_start.status_code == 200
    assert resp_start.json()["is_started"] is True

    # 7. Two-phase handler handoff
    # Host initiates handoff to attendee
    resp_xfer1 = client.post(
        f"/events/{evt_id}/transfer-host",
        headers=host_headers,
        json={"recipient_user_id": attendee_id},
    )
    assert resp_xfer1.status_code == 200

    # Attendee attempts acceptance from outside radius -> 403
    resp_xfer2_outside = client.post(
        f"/events/{evt_id}/accept-transfer",
        headers=attendee_headers,
        json={
            "latitude": 40.7000,
            "longitude": -74.0000,
            "accuracy_meters": 10.0,
            "recorded_at": now_iso,
        },
    )
    assert resp_xfer2_outside.status_code == 403

    # Attendee accepts inside radius -> 200 OK
    resp_xfer2 = client.post(
        f"/events/{evt_id}/accept-transfer",
        headers=attendee_headers,
        json={
            "latitude": 40.7580,
            "longitude": -73.9855,
            "accuracy_meters": 10.0,
            "recorded_at": now_iso,
        },
    )
    assert resp_xfer2.status_code == 200
    updated_evt = resp_xfer2.json()
    assert updated_evt["host_id"] == attendee_id
    assert updated_evt["is_host"] is True

    # Old host is now regular attendee and cannot end the event
    resp_old_end = client.post(f"/events/{evt_id}/end", headers=host_headers)
    assert resp_old_end.status_code == 403

    # New host ends the event -> 200
    resp_end = client.post(f"/events/{evt_id}/end", headers=attendee_headers)
    assert resp_end.status_code == 200
    assert resp_end.json()["is_ended"] is True
