from datetime import datetime, timezone, timedelta
from uuid import uuid4
import pytest
from app.models import Landmark, LandmarkVisit, User
from app.location import make_geography_point


def test_nearby_discovery_privacy_and_suppression(client, db_session):
    # 1. Register Alice, Bob, and Charlie
    r_alice = client.post("/auth/register", json={
        "username": "alice_near",
        "email": "alice_near@example.com",
        "password": "Password123!",
    })
    alice_token = r_alice.json()["access_token"]
    alice_headers = {"Authorization": f"Bearer {alice_token}"}
    alice_id = client.get("/users/me", headers=alice_headers).json()["id"]

    r_bob = client.post("/auth/register", json={
        "username": "bob_near",
        "email": "bob_near@example.com",
        "password": "Password123!",
    })
    bob_token = r_bob.json()["access_token"]
    bob_headers = {"Authorization": f"Bearer {bob_token}"}
    bob_id = client.get("/users/me", headers=bob_headers).json()["id"]

    r_charlie = client.post("/auth/register", json={
        "username": "charlie_near",
        "email": "charlie_near@example.com",
        "password": "Password123!",
    })
    charlie_token = r_charlie.json()["access_token"]
    charlie_headers = {"Authorization": f"Bearer {charlie_token}"}
    charlie_id = client.get("/users/me", headers=charlie_headers).json()["id"]

    # 2. Create Landmark A (within 100m of Alice) and Landmark B (far away, 10km)
    lm_near = Landmark(
        id=uuid4(),
        name="Nearby Cafe",
        area_name="Greenwich Village",
        latitude=40.7336,
        longitude=-74.0027,
        location=make_geography_point(-74.0027, 40.7336),
        radius_meters=100.0,
    )
    lm_far = Landmark(
        id=uuid4(),
        name="Far Park",
        area_name="Uptown",
        latitude=40.8200,
        longitude=-73.9400,
        location=make_geography_point(-73.9400, 40.8200),
        radius_meters=200.0,
    )
    db_session.add_all([lm_near, lm_far])
    db_session.flush()

    now_iso = datetime.now(timezone.utc).isoformat()

    # Bob enters nearby cafe
    client.post(
        f"/landmarks/{lm_near.id}/enter",
        headers=bob_headers,
        json={"latitude": 40.7336, "longitude": -74.0027, "accuracy_meters": 10.0, "recorded_at": now_iso},
    )

    # Charlie enters far park
    client.post(
        f"/landmarks/{lm_far.id}/enter",
        headers=charlie_headers,
        json={"latitude": 40.8200, "longitude": -73.9400, "accuracy_meters": 10.0, "recorded_at": now_iso},
    )

    # 3. Alice discovers nearby users from Greenwich Village
    resp = client.post(
        "/nearby",
        headers=alice_headers,
        json={"latitude": 40.7336, "longitude": -74.0027, "accuracy_meters": 10.0, "recorded_at": now_iso},
    )
    assert resp.status_code == 200
    participants = resp.json()

    # Alice (You) and Bob should be in results; Charlie (10km away) should be excluded
    p_ids = [p["user_id"] for p in participants]
    assert alice_id in p_ids
    assert bob_id in p_ids
    assert charlie_id not in p_ids

    # Invariant: Raw GPS coordinates are never returned
    for p in participants:
        assert "latitude" not in p
        assert "longitude" not in p
        assert "distance_label" in p
        assert "area_name" in p

    # 4. Invariant: Bob turns on Ghost Mode -> instantly vanishes from Alice nearby discovery
    client.post("/users/me/ghost-mode", headers=bob_headers)

    resp_after_ghost = client.post(
        "/nearby",
        headers=alice_headers,
        json={"latitude": 40.7336, "longitude": -74.0027, "accuracy_meters": 10.0, "recorded_at": now_iso},
    )
    p_ids_after_ghost = [p["user_id"] for p in resp_after_ghost.json()]
    assert bob_id not in p_ids_after_ghost

    # 5. Bob turns off Ghost Mode, re-enters cafe, but Alice blocks Bob
    client.post("/users/me/ghost-mode", headers=bob_headers)
    client.post(
        f"/landmarks/{lm_near.id}/enter",
        headers=bob_headers,
        json={"latitude": 40.7336, "longitude": -74.0027, "accuracy_meters": 10.0, "recorded_at": now_iso},
    )
    # Alice blocks Bob
    client.post(f"/users/{bob_id}/block", headers=alice_headers)

    resp_after_block = client.post(
        "/nearby",
        headers=alice_headers,
        json={"latitude": 40.7336, "longitude": -74.0027, "accuracy_meters": 10.0, "recorded_at": now_iso},
    )
    p_ids_after_block = [p["user_id"] for p in resp_after_block.json()]
    assert bob_id not in p_ids_after_block
