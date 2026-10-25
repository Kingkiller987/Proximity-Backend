import struct
import pytest
from uuid import uuid4

def create_minimal_png(width=100, height=200):
    return (
        b"\x89PNG\r\n\x1a\n"
        b"\x00\x00\x00\rIHDR"
        + struct.pack(">II", width, height)
        + b"\x08\x02\x00\x00\x00\x90wS\xde"
        b"\x00\x00\x00\x0cIDATx\x9cc\xf8\xff\xff?\x00\x05\xfe\x02\xfe\xa7V\x01\x81"
        b"\x00\x00\x00\x00IEND\xaeB`\x82"
    )

def register_and_get_user(client, username, email):
    res = client.post("/auth/register", json={
        "username": username,
        "email": email,
        "password": "Password123!",
    })
    assert res.status_code == 201
    token = res.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    me = client.get("/users/me", headers=headers).json()
    return me, headers

def test_dm_request_flow_and_idempotency(client):
    user_a, headers_a = register_and_get_user(client, "alice_dm", "alice_dm@example.com")
    user_b, headers_b = register_and_get_user(client, "bob_dm", "bob_dm@example.com")

    # 1. Send DM request from Alice to Bob
    req_res = client.post("/dm/requests", json={
        "recipient_id": user_b["id"],
        "intro_message": "Hey Bob, let us chat!",
    }, headers=headers_a)
    assert req_res.status_code == 201
    conv = req_res.json()
    assert conv["status"] == "pending"
    assert conv["intro_message"] == "Hey Bob, let us chat!"
    assert conv["initiator_id"] == user_a["id"]
    assert conv["recipient_id"] == user_b["id"]
    assert conv["is_incoming"] is False
    assert conv["other_user"]["user_id"] == user_b["id"]
    assert conv["other_user"]["display_name"] == "@bob_dm"

    conv_id = conv["id"]

    # 2. Idempotency: Alice sends request again to Bob
    req2_res = client.post("/dm/requests", json={
        "recipient_id": user_b["id"],
        "intro_message": "Another message",
    }, headers=headers_a)
    assert req2_res.status_code == 201
    assert req2_res.json()["id"] == conv_id
    assert req2_res.json()["status"] == "pending"

    # 3. Check Bob inbox
    inbox_b = client.get("/dm/inbox", headers=headers_b)
    assert inbox_b.status_code == 200
    b_threads = inbox_b.json()
    assert len(b_threads) == 1
    assert b_threads[0]["id"] == conv_id
    assert b_threads[0]["is_incoming"] is True
    assert b_threads[0]["status"] == "pending"
    assert b_threads[0]["other_user"]["user_id"] == user_a["id"]
    assert b_threads[0]["other_user"]["display_name"] == "@alice_dm"

    # 4. Check Alice inbox
    inbox_a = client.get("/dm/inbox", headers=headers_a)
    assert inbox_a.status_code == 200
    a_threads = inbox_a.json()
    assert len(a_threads) == 1
    assert a_threads[0]["id"] == conv_id
    assert a_threads[0]["is_incoming"] is False
    assert a_threads[0]["status"] == "pending"

    # 5. Get conversation by ID
    conv_get = client.get(f"/dm/conversations/{conv_id}", headers=headers_a)
    assert conv_get.status_code == 200
    assert conv_get.json()["id"] == conv_id


def test_dm_cannot_message_self_or_nonexistent(client):
    user, headers = register_and_get_user(client, "solo_dm", "solo_dm@example.com")

    # Message self
    res_self = client.post("/dm/requests", json={
        "recipient_id": user["id"],
        "intro_message": "Talking to myself",
    }, headers=headers)
    assert res_self.status_code == 400

    # Message nonexistent
    res_missing = client.post("/dm/requests", json={
        "recipient_id": str(uuid4()),
        "intro_message": "Hello ghost",
    }, headers=headers)
    assert res_missing.status_code == 404


def test_dm_block_enforcement(client):
    user_a, headers_a = register_and_get_user(client, "charlie_dm", "charlie_dm@example.com")
    user_b, headers_b = register_and_get_user(client, "dave_dm", "dave_dm@example.com")

    # Charlie blocks Dave
    block_res = client.post(f"/users/{user_b["id"]}/block", headers=headers_a)
    assert block_res.status_code == 204

    # Dave tries to send DM to Charlie -> 403
    dm_res = client.post("/dm/requests", json={
        "recipient_id": user_a["id"],
        "intro_message": "Let me in",
    }, headers=headers_b)
    assert dm_res.status_code == 403


def test_dm_response_and_messaging_and_media(client):
    user_a, headers_a = register_and_get_user(client, "eve_dm", "eve_dm@example.com")
    user_b, headers_b = register_and_get_user(client, "frank_dm", "frank_dm@example.com")

    # Eve sends DM request to Frank
    req = client.post("/dm/requests", json={
        "recipient_id": user_b["id"],
        "intro_message": "Hi Frank",
    }, headers=headers_a)
    assert req.status_code == 201
    conv_id = req.json()["id"]

    # 1. Eve cannot post message while pending
    msg_fail = client.post(f"/dm/conversations/{conv_id}/messages", json={
        "body": "Hello before accept",
    }, headers=headers_a)
    assert msg_fail.status_code == 403

    # 2. Eve cannot upload media while pending
    png_bytes = create_minimal_png(50, 50)
    upload_fail = client.post("/media/upload", data={
        "channel_kind": "dm",
        "channel_id": conv_id,
    }, files={"file": ("photo.png", png_bytes, "image/png")}, headers=headers_a)
    assert upload_fail.status_code == 403

    # 3. Eve cannot respond to her own request
    resp_fail = client.post(f"/dm/conversations/{conv_id}/respond", json={
        "accept": True,
    }, headers=headers_a)
    assert resp_fail.status_code == 403

    # 4. Frank accepts the request
    accept_res = client.post(f"/dm/conversations/{conv_id}/respond", json={
        "accept": True,
    }, headers=headers_b)
    assert accept_res.status_code == 200
    assert accept_res.json()["status"] == "accepted"

    # 5. Eve sends direct message
    msg_a = client.post(f"/dm/conversations/{conv_id}/messages", json={
        "body": "Awesome, thanks for accepting!",
    }, headers=headers_a)
    assert msg_a.status_code == 201
    assert msg_a.json()["sequence"] == 1
    assert msg_a.json()["sender_id"] == user_a["id"]

    # 6. Frank uploads image and sends direct message with attachment
    upload_ok = client.post("/media/upload", data={
        "channel_kind": "dm",
        "channel_id": conv_id,
    }, files={"file": ("frank_photo.png", png_bytes, "image/png")}, headers=headers_b)
    assert upload_ok.status_code == 201
    media_id = upload_ok.json()["media_id"]

    msg_b = client.post(f"/dm/conversations/{conv_id}/messages", json={
        "body": "Check out this photo!",
        "attachment_id": media_id,
    }, headers=headers_b)
    assert msg_b.status_code == 201
    assert msg_b.json()["sequence"] == 2
    assert msg_b.json()["attachment_id"] == media_id

    # 7. Eve views messages
    msgs_res = client.get(f"/dm/conversations/{conv_id}/messages", headers=headers_a)
    assert msgs_res.status_code == 200
    msgs = msgs_res.json()
    assert len(msgs) == 2
    assert msgs[0]["body"] == "Awesome, thanks for accepting!"
    assert msgs[1]["attachment_id"] == media_id

    # 8. Download token generation by Eve (recipient of the photo)
    token_res = client.post(f"/media/{media_id}/token", headers=headers_a)
    assert token_res.status_code == 200
    dl_token = token_res.json()["token"]

    # Download image using token
    dl_res = client.get(f"/media/{media_id}?token={dl_token}")
    assert dl_res.status_code == 200
    assert dl_res.content == png_bytes
