import struct
import pytest
from uuid import uuid4
from datetime import datetime, timezone
from app.models import Landmark, LandmarkVisit, User
from app.location import make_geography_point


def create_minimal_png(width=100, height=200):
    return (
        b"\x89PNG\r\n\x1a\n"
        b"\x00\x00\x00\rIHDR"
        + struct.pack(">II", width, height)
        + b"\x08\x02\x00\x00\x00\x90wS\xde"
        b"\x00\x00\x00\x0cIDATx\x9cc\xf8\xff\xff?\x00\x05\xfe\x02\xfe\xa7V\x01\x81"
        b"\x00\x00\x00\x00IEND\xaeB`\x82"
    )


def test_media_upload_and_magic_byte_validation(client, db_session):
    # Register user
    reg = client.post("/auth/register", json={
        "username": "media_user",
        "email": "media_user@example.com",
        "password": "SecurePassword123!",
    })
    assert reg.status_code == 201
    token = reg.json()["access_token"]
    auth_headers = {"Authorization": f"Bearer {token}"}

    # Create landmark
    lm = Landmark(
        id=uuid4(),
        name="Media Landmark",
        area_name="Downtown",
        latitude=40.7128,
        longitude=-74.0060,
        location=make_geography_point(-74.0060, 40.7128),
        radius_meters=100.0,
    )
    db_session.add(lm)
    db_session.flush()

    # 1. Attempt upload to landmark without active visit -> 403
    png_data = create_minimal_png(150, 250)
    resp = client.post(
        "/media/upload",
        headers=auth_headers,
        data={"channel_kind": "landmark", "channel_id": str(lm.id)},
        files={"file": ("test.png", png_data, "image/png")},
    )
    assert resp.status_code == 403
    assert "active landmark visit" in resp.json()["detail"].lower()

    # 2. Enter landmark
    now_iso = datetime.now(timezone.utc).isoformat()
    enter_resp = client.post(
        f"/landmarks/{lm.id}/enter",
        headers=auth_headers,
        json={
            "latitude": 40.7128,
            "longitude": -74.0060,
            "accuracy_meters": 10.0,
            "recorded_at": now_iso,
        },
    )
    assert enter_resp.status_code == 200

    # 3. Upload valid PNG with active visit -> 201 Created
    resp = client.post(
        "/media/upload",
        headers=auth_headers,
        data={"channel_kind": "landmark", "channel_id": str(lm.id)},
        files={"file": ("test.png", png_data, "image/png")},
    )
    assert resp.status_code == 201
    upload_data = resp.json()
    assert upload_data["mime_type"] == "image/png"
    assert upload_data["width"] == 150
    assert upload_data["height"] == 250

    # 4. Reject spoofed MIME type (text file claimed as image/png)
    fake_data = b"This is plain text pretending to be an image"
    resp = client.post(
        "/media/upload",
        headers=auth_headers,
        data={"channel_kind": "landmark", "channel_id": str(lm.id)},
        files={"file": ("fake.png", fake_data, "image/png")},
    )
    assert resp.status_code == 400
    assert "unsupported file format" in resp.json()["detail"].lower()

    # 5. Reject file exceeding 5MB limit
    large_data = b"\x89PNG\r\n\x1a\n" + b"0" * (5 * 1024 * 1024 + 10)
    resp = client.post(
        "/media/upload",
        headers=auth_headers,
        data={"channel_kind": "landmark", "channel_id": str(lm.id)},
        files={"file": ("large.png", large_data, "image/png")},
    )
    assert resp.status_code == 413


def test_media_download_token_and_authorization_revocation(client, db_session):
    # Create users
    r1 = client.post("/auth/register", json={
        "username": "alice_media",
        "email": "alice_media@example.com",
        "password": "Password123!",
    })
    alice_token = r1.json()["access_token"]
    alice_headers = {"Authorization": f"Bearer {alice_token}"}

    r2 = client.post("/auth/register", json={
        "username": "bob_media",
        "email": "bob_media@example.com",
        "password": "Password123!",
    })
    bob_token = r2.json()["access_token"]
    bob_headers = {"Authorization": f"Bearer {bob_token}"}

    # Create landmark
    lm = Landmark(
        id=uuid4(),
        name="Photo Landmark",
        area_name="Center",
        latitude=37.7749,
        longitude=-122.4194,
        location=make_geography_point(-122.4194, 37.7749),
        radius_meters=150.0,
    )
    db_session.add(lm)
    db_session.flush()

    now_iso = datetime.now(timezone.utc).isoformat()
    # Alice enters and uploads image
    client.post(
        f"/landmarks/{lm.id}/enter",
        headers=alice_headers,
        json={"latitude": 37.7749, "longitude": -122.4194, "accuracy_meters": 5.0, "recorded_at": now_iso},
    )
    png_bytes = create_minimal_png(64, 64)
    upload_resp = client.post(
        "/media/upload",
        headers=alice_headers,
        data={"channel_kind": "landmark", "channel_id": str(lm.id)},
        files={"file": ("avatar.png", png_bytes, "image/png")},
    )
    media_id = upload_resp.json()["media_id"]

    # Bob has NOT entered the landmark -> cannot get download token
    tok_resp = client.post(f"/media/{media_id}/token", headers=bob_headers)
    assert tok_resp.status_code == 403

    # Direct download without auth or visit -> 403
    dl_resp = client.get(f"/media/{media_id}", headers=bob_headers)
    assert dl_resp.status_code == 403

    # Alice issues a download token for herself
    alice_tok_resp = client.post(f"/media/{media_id}/token", headers=alice_headers)
    assert alice_tok_resp.status_code == 200
    dl_token = alice_tok_resp.json()["token"]

    # Alice downloads with token -> 200 OK
    dl_resp = client.get(f"/media/{media_id}?token={dl_token}")
    assert dl_resp.status_code == 200
    assert dl_resp.headers["content-type"] == "image/png"
    assert dl_resp.content == png_bytes

    # Alice leaves the landmark
    leave_resp = client.post(f"/landmarks/{lm.id}/leave", headers=alice_headers)
    assert leave_resp.status_code in (200, 204)

    # Now Alice tries to download again using the previous token -> 403 Forbidden!
    revoked_resp = client.get(f"/media/{media_id}?token={dl_token}")
    assert revoked_resp.status_code == 403
