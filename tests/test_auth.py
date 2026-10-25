import pytest
from app.models import User, RefreshSession
from app.security import verify_password


def test_register_success(client):
    res = client.post("/auth/register", json={
        "username": "alice",
        "email": "alice@example.com",
        "password": "Password123!",
    })
    assert res.status_code == 201
    data = res.json()
    assert "access_token" in data
    assert "refresh_token" in data
    assert data["token_type"] == "bearer"


def test_register_duplicate_username_fails(client):
    res = client.post("/auth/register", json={
        "username": "bob_dup",
        "email": "bob1@example.com",
        "password": "Password123!",
    })
    assert res.status_code == 201

    res_dup = client.post("/auth/register", json={
        "username": "Bob_Dup",
        "email": "bob2@example.com",
        "password": "Password123!",
    })
    assert res_dup.status_code == 409
    assert "Username is already taken" in res_dup.json()["detail"]


def test_register_invalid_format_rejected(client):
    res = client.post("/auth/register", json={
        "username": "a!",
        "email": "invalid-email",
        "password": "short",
    })
    assert res.status_code == 422


def test_login_success_and_argon2_verification(client, db_session):
    client.post("/auth/register", json={
        "username": "charlie",
        "email": "charlie@example.com",
        "password": "StrongPassword99!",
    })

    user = db_session.query(User).filter(User.username == "charlie").one()
    assert user.password_hash.startswith("$argon2id$")
    assert verify_password("StrongPassword99!", user.password_hash)

    login_res = client.post("/auth/login", json={
        "username": "Charlie",
        "password": "StrongPassword99!",
    })
    assert login_res.status_code == 200
    assert "access_token" in login_res.json()

    bad_login = client.post("/auth/login", json={
        "username": "charlie",
        "password": "WrongPassword",
    })
    assert bad_login.status_code == 401


def test_refresh_token_rotation_and_replay_protection(client):
    reg = client.post("/auth/register", json={
        "username": "dana",
        "email": "dana@example.com",
        "password": "Password123!",
    })
    refresh_1 = reg.json()["refresh_token"]

    rot_res = client.post("/auth/refresh", json={"refresh_token": refresh_1})
    assert rot_res.status_code == 200
    refresh_2 = rot_res.json()["refresh_token"]
    assert refresh_2 != refresh_1

    replay_res = client.post("/auth/refresh", json={"refresh_token": refresh_1})
    assert replay_res.status_code == 401


def test_logout_revokes_token(client):
    reg = client.post("/auth/register", json={
        "username": "elena",
        "email": "elena@example.com",
        "password": "Password123!",
    })
    refresh_token = reg.json()["refresh_token"]

    logout_res = client.post("/auth/logout", json={"refresh_token": refresh_token})
    assert logout_res.status_code == 204

    res = client.post("/auth/refresh", json={"refresh_token": refresh_token})
    assert res.status_code == 401


def test_get_my_profile_requires_auth(client):
    unauth = client.get("/users/me")
    assert unauth.status_code == 401

    reg = client.post("/auth/register", json={
        "username": "frank",
        "email": "frank@example.com",
        "password": "Password123!",
    })
    token = reg.json()["access_token"]

    auth_res = client.get("/users/me", headers={"Authorization": f"Bearer {token}"})
    assert auth_res.status_code == 200
    assert auth_res.json()["username"] == "frank"


def test_anonymous_profile_projection_privacy(client):
    # User 1: Anon
    res1 = client.post("/auth/register", json={
        "username": "grace_anon",
        "email": "grace@example.com",
        "password": "Password123!",
    })
    token1 = res1.json()["access_token"]
    me1 = client.get("/users/me", headers={"Authorization": f"Bearer {token1}"}).json()
    user1_id = me1["id"]

    # Toggle to anonymous mode
    client.patch("/users/me", json={"identity_mode": "anonymous"}, headers={"Authorization": f"Bearer {token1}"})

    # User 2: Viewer
    res2 = client.post("/auth/register", json={
        "username": "helen_viewer",
        "email": "helen@example.com",
        "password": "Password123!",
    })
    token2 = res2.json()["access_token"]

    pub = client.get(f"/users/{user1_id}", headers={"Authorization": f"Bearer {token2}"})
    assert pub.status_code == 200
    data = pub.json()
    assert data["is_anonymous"] is True
    assert data["display_name"] == me1["anonymous_handle"]
    assert "grace" not in data["display_name"].lower()


def test_bidirectional_blocking_suppression(client):
    res_a = client.post("/auth/register", json={
        "username": "ivan_a",
        "email": "ivan@example.com",
        "password": "Password123!",
    })
    token_a = res_a.json()["access_token"]
    id_a = client.get("/users/me", headers={"Authorization": f"Bearer {token_a}"}).json()["id"]

    res_b = client.post("/auth/register", json={
        "username": "judy_b",
        "email": "judy@example.com",
        "password": "Password123!",
    })
    token_b = res_b.json()["access_token"]
    id_b = client.get("/users/me", headers={"Authorization": f"Bearer {token_b}"}).json()["id"]

    # Block B from A
    blk = client.post(f"/users/{id_b}/block", headers={"Authorization": f"Bearer {token_a}"})
    assert blk.status_code == 204

    # Neither can view each other
    assert client.get(f"/users/{id_b}", headers={"Authorization": f"Bearer {token_a}"}).status_code == 404
    assert client.get(f"/users/{id_a}", headers={"Authorization": f"Bearer {token_b}"}).status_code == 404

    # Unblock
    unblk = client.delete(f"/users/{id_b}/block", headers={"Authorization": f"Bearer {token_a}"})
    assert unblk.status_code == 204
    assert client.get(f"/users/{id_b}", headers={"Authorization": f"Bearer {token_a}"}).status_code == 200
