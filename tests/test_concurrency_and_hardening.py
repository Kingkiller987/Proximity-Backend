from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from uuid import uuid4
import pytest
from starlette.testclient import TestClient
from app.main import app
from app.database import get_db, get_test_db, TestSessionLocal
from app.models import Landmark, LandmarkVisit, Event
from app.location import make_geography_point


@pytest.fixture
def multi_conn_client():
    app.dependency_overrides[get_db] = get_test_db
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def test_concurrent_refresh_rotation_mutex(multi_conn_client):
    uid = uuid4().hex[:8]
    reg = multi_conn_client.post("/auth/register", json={
        "username": f"rot_{uid}",
        "email": f"rot_{uid}@example.com",
        "password": "Password123!",
    })
    assert reg.status_code == 201
    refresh_token = reg.json()["refresh_token"]

    def attempt_refresh():
        c = TestClient(app)
        return c.post("/auth/refresh", json={"refresh_token": refresh_token}).status_code

    with ThreadPoolExecutor(max_workers=2) as ex:
        f1 = ex.submit(attempt_refresh)
        f2 = ex.submit(attempt_refresh)
        statuses = [f1.result(), f2.result()]

    # Exactly one refresh succeeds, the concurrent duplicate is rejected with 401
    assert sorted(statuses) == [200, 401]


def test_concurrent_landmark_message_monotonic_sequence(multi_conn_client):
    db = TestSessionLocal()
    lm_id = uuid4()
    lm = Landmark(
        id=lm_id,
        name="Concurrent LM",
        area_name="Downtown",
        latitude=40.7128,
        longitude=-74.0060,
        location=make_geography_point(-74.0060, 40.7128),
        radius_meters=100.0,
    )
    db.add(lm)
    db.commit()
    db.close()

    uid = uuid4().hex[:8]
    reg = multi_conn_client.post("/auth/register", json={
        "username": f"seq_{uid}",
        "email": f"seq_{uid}@example.com",
        "password": "Password123!",
    })
    assert reg.status_code == 201
    token = reg.json()["access_token"]
    auth_headers = {"Authorization": f"Bearer {token}"}

    now_iso = datetime.now(timezone.utc).isoformat()
    enter_res = multi_conn_client.post(f"/landmarks/{lm_id}/enter", headers=auth_headers, json={
        "latitude": 40.7128, "longitude": -74.0060, "accuracy_meters": 5.0, "recorded_at": now_iso
    })
    assert enter_res.status_code == 200

    def send_msg(idx):
        c = TestClient(app)
        resp = c.post(f"/landmarks/{lm_id}/messages", headers=auth_headers, json={
            "body": f"Parallel message {idx}",
            "location_fix": {
                "latitude": 40.7128,
                "longitude": -74.0060,
                "accuracy_meters": 5.0,
                "recorded_at": datetime.now(timezone.utc).isoformat(),
            },
        })
        return resp.status_code, resp.json().get("sequence")

    with ThreadPoolExecutor(max_workers=4) as ex:
        results = list(ex.map(send_msg, range(8)))

    assert all(r[0] == 201 for r in results)
    seqs = [r[1] for r in results]
    # Invariant: Monotonic unique sequences with no gaps or collisions
    assert len(seqs) == 8
    assert len(set(seqs)) == 8
    assert sorted(seqs) == list(range(1, 9))


def test_concurrent_host_transfer_acceptance_race(multi_conn_client):
    uid_h = uuid4().hex[:8]
    r_h = multi_conn_client.post("/auth/register", json={
        "username": f"h_{uid_h}",
        "email": f"h_{uid_h}@example.com",
        "password": "Password123!",
    })
    assert r_h.status_code == 201
    host_headers = {"Authorization": f"Bearer {r_h.json()['access_token']}"}

    uid_r = uuid4().hex[:8]
    r_r = multi_conn_client.post("/auth/register", json={
        "username": f"r_{uid_r}",
        "email": f"r_{uid_r}@example.com",
        "password": "Password123!",
    })
    assert r_r.status_code == 201
    recip_headers = {"Authorization": f"Bearer {r_r.json()['access_token']}"}
    recip_id = multi_conn_client.get("/users/me", headers=recip_headers).json()["id"]

    now_utc = datetime.now(timezone.utc)
    evt = multi_conn_client.post("/events", headers=host_headers, json={
        "title": "Race Handoff",
        "latitude": 40.7580,
        "longitude": -73.9855,
        "area_name": "Times Square",
        "radius_meters": 100.0,
        "starts_at": now_utc.isoformat(),
        "access_type": "public",
        "default_open_minutes": 0,
        "default_close_minutes": 0,
    }).json()
    evt_id = evt["id"]

    multi_conn_client.post(f"/events/{evt_id}/join", headers=recip_headers, json={})
    now_iso = now_utc.isoformat()
    multi_conn_client.post(f"/events/{evt_id}/start", headers=host_headers, json={
        "latitude": 40.7580, "longitude": -73.9855, "accuracy_meters": 5.0, "recorded_at": now_iso
    })

    # Initiate handoff
    multi_conn_client.post(f"/events/{evt_id}/transfer-host", headers=host_headers, json={"recipient_user_id": recip_id})

    def attempt_accept():
        c = TestClient(app)
        return c.post(f"/events/{evt_id}/accept-transfer", headers=recip_headers, json={
            "latitude": 40.7580, "longitude": -73.9855, "accuracy_meters": 5.0, "recorded_at": datetime.now(timezone.utc).isoformat()
        }).status_code

    with ThreadPoolExecutor(max_workers=2) as ex:
        f1 = ex.submit(attempt_accept)
        f2 = ex.submit(attempt_accept)
        statuses = [f1.result(), f2.result()]

    # Invariant: Row lock serializes acceptance; exactly one 200 and one 403
    assert sorted(statuses) == [200, 403]
