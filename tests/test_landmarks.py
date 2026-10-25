from datetime import datetime, timezone, timedelta
import pytest
from app.models import Landmark
from app.location import make_geography_point


@pytest.fixture
def landmark_fixture(db_session):
    # Stony Brook Staller Steps (40.9157, -73.1235), radius 100m
    lm = Landmark(
        name="Staller Steps",
        area_name="Stony Brook",
        latitude=40.9157,
        longitude=-73.1235,
        location=make_geography_point(-73.1235, 40.9157),
        radius_meters=100.0,
    )
    db_session.add(lm)
    db_session.flush()
    return lm


def get_auth_token(client, username="user_lm"):
    res = client.post("/auth/register", json={
        "username": username,
        "email": f"{username}@example.com",
        "password": "Password123!",
    })
    return res.json()["access_token"]


def test_landmark_discovery_distance(client, landmark_fixture):
    # Point exactly at landmark center
    res = client.get("/landmarks?latitude=40.9157&longitude=-73.1235")
    assert res.status_code == 200
    data = res.json()
    assert len(data) >= 1
    found = next(lm for lm in data if lm["id"] == str(landmark_fixture.id))
    assert found["is_user_inside"] is True
    assert found["distance_label"] == "< 100m away"


def test_location_freshness_and_accuracy_validation(client, landmark_fixture):
    token = get_auth_token(client, "loc_tester")
    headers = {"Authorization": f"Bearer {token}"}
    lm_id = str(landmark_fixture.id)

    # 1. Stale fix (>30s)
    stale_time = (datetime.now(timezone.utc) - timedelta(seconds=35)).isoformat()
    stale_res = client.post(f"/landmarks/{lm_id}/enter", json={
        "latitude": 40.9157,
        "longitude": -73.1235,
        "accuracy_meters": 10.0,
        "recorded_at": stale_time,
    }, headers=headers)
    assert stale_res.status_code == 400
    assert "stale" in stale_res.json()["detail"].lower()

    # 2. Inaccurate fix (>50m)
    now_iso = datetime.now(timezone.utc).isoformat()
    inacc_res = client.post(f"/landmarks/{lm_id}/enter", json={
        "latitude": 40.9157,
        "longitude": -73.1235,
        "accuracy_meters": 65.0,
        "recorded_at": now_iso,
    }, headers=headers)
    assert inacc_res.status_code == 400
    assert "accuracy" in inacc_res.json()["detail"].lower()


def test_enter_outside_radius_rejected(client, landmark_fixture):
    token = get_auth_token(client, "far_user")
    headers = {"Authorization": f"Bearer {token}"}
    lm_id = str(landmark_fixture.id)

    # Coordinates 5km away
    now_iso = datetime.now(timezone.utc).isoformat()
    res = client.post(f"/landmarks/{lm_id}/enter", json={
        "latitude": 40.9500,
        "longitude": -73.1500,
        "accuracy_meters": 10.0,
        "recorded_at": now_iso,
    }, headers=headers)
    assert res.status_code == 403
    assert "Outside authorized landmark radius" in res.json()["detail"]


def test_landmark_visit_isolation_and_reentry(client, landmark_fixture):
    token_a = get_auth_token(client, "user_alpha")
    headers_a = {"Authorization": f"Bearer {token_a}"}

    token_b = get_auth_token(client, "user_beta")
    headers_b = {"Authorization": f"Bearer {token_b}"}

    lm_id = str(landmark_fixture.id)
    now_iso = datetime.now(timezone.utc).isoformat()

    # Step 1: User A enters inside radius
    enter_a = client.post(f"/landmarks/{lm_id}/enter", json={
        "latitude": 40.9157,
        "longitude": -73.1235,
        "accuracy_meters": 5.0,
        "recorded_at": now_iso,
    }, headers=headers_a)
    assert enter_a.status_code == 200
    assert enter_a.json()["is_active"] is True
    assert enter_a.json()["min_message_sequence"] == 1

    # Step 2: User A posts Message 1
    msg1_res = client.post(f"/landmarks/{lm_id}/messages", json={
        "body": "First visit message from Alpha",
        "location_fix": {
            "latitude": 40.9157,
            "longitude": -73.1235,
            "accuracy_meters": 5.0,
            "recorded_at": datetime.now(timezone.utc).isoformat(),
        }
    }, headers=headers_a)
    assert msg1_res.status_code == 201
    assert msg1_res.json()["sequence"] == 1

    # User A sees Message 1
    msgs_a1 = client.get(f"/landmarks/{lm_id}/messages", headers=headers_a).json()
    assert len(msgs_a1) == 1
    assert msgs_a1[0]["body"] == "First visit message from Alpha"

    # Step 3: User A leaves the landmark
    leave_res = client.post(f"/landmarks/{lm_id}/leave", headers=headers_a)
    assert leave_res.status_code == 204

    # Now reading messages fails for User A
    assert client.get(f"/landmarks/{lm_id}/messages", headers=headers_a).status_code == 403

    # Step 4: User B enters and posts Message 2
    client.post(f"/landmarks/{lm_id}/enter", json={
        "latitude": 40.9157,
        "longitude": -73.1235,
        "accuracy_meters": 5.0,
        "recorded_at": datetime.now(timezone.utc).isoformat(),
    }, headers=headers_b)

    msg2_res = client.post(f"/landmarks/{lm_id}/messages", json={
        "body": "Message from Beta while Alpha is away",
        "location_fix": {
            "latitude": 40.9157,
            "longitude": -73.1235,
            "accuracy_meters": 5.0,
            "recorded_at": datetime.now(timezone.utc).isoformat(),
        }
    }, headers=headers_b)
    assert msg2_res.status_code == 201
    assert msg2_res.json()["sequence"] == 2

    # Step 5: User A re-enters the landmark (NEW visit session)
    enter_a2 = client.post(f"/landmarks/{lm_id}/enter", json={
        "latitude": 40.9157,
        "longitude": -73.1235,
        "accuracy_meters": 5.0,
        "recorded_at": datetime.now(timezone.utc).isoformat(),
    }, headers=headers_a)
    assert enter_a2.status_code == 200
    # Invariant: Min sequence boundary is strictly after Message 2 (sequence 3)
    assert enter_a2.json()["min_message_sequence"] == 3

    # User A reads messages: MUST BE EMPTY! Message 1 and Message 2 are inaccessible!
    msgs_a2 = client.get(f"/landmarks/{lm_id}/messages", headers=headers_a).json()
    assert len(msgs_a2) == 0

    # Step 6: User B posts Message 3
    client.post(f"/landmarks/{lm_id}/messages", json={
        "body": "Message 3 after Alpha re-entry",
        "location_fix": {
            "latitude": 40.9157,
            "longitude": -73.1235,
            "accuracy_meters": 5.0,
            "recorded_at": datetime.now(timezone.utc).isoformat(),
        }
    }, headers=headers_b)

    # Now User A sees ONLY Message 3!
    msgs_a3 = client.get(f"/landmarks/{lm_id}/messages", headers=headers_a).json()
    assert len(msgs_a3) == 1
    assert msgs_a3[0]["body"] == "Message 3 after Alpha re-entry"
    assert msgs_a3[0]["sequence"] == 3


def test_ghost_mode_instantly_terminates_landmark_visit(client, landmark_fixture):
    token = get_auth_token(client, "ghost_tester")
    headers = {"Authorization": f"Bearer {token}"}
    lm_id = str(landmark_fixture.id)

    # Enter landmark
    enter = client.post(f"/landmarks/{lm_id}/enter", json={
        "latitude": 40.9157,
        "longitude": -73.1235,
        "accuracy_meters": 5.0,
        "recorded_at": datetime.now(timezone.utc).isoformat(),
    }, headers=headers)
    assert enter.status_code == 200

    # Enable ghost mode
    ghost_res = client.post("/users/me/ghost-mode", headers=headers)
    assert ghost_res.status_code == 200
    assert ghost_res.json()["is_ghost_mode"] is True

    # Chat access is immediately revoked
    chat_res = client.get(f"/landmarks/{lm_id}/messages", headers=headers)
    assert chat_res.status_code == 403
