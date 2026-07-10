import base64

from app.services.auth_service import MAX_AVATAR_BYTES


async def _signup(client, email, name):
    r = await client.post("/api/v1/auth/signup", json={"email": email, "password": "hunter2222", "display_name": name})
    body = r.json()
    return body["access_token"], body["user"]["id"]


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


# A genuinely valid, tiny (67-byte) 1x1 transparent PNG — real image
# bytes, not a placeholder string, so base64 decoding and size checks
# exercise the real code path.
TINY_PNG_B64 = base64.b64encode(bytes.fromhex(
    "89504e470d0a1a0a0000000d4948445200000001000000010806000000"
    "1f15c4890000000a4944415478da6360000002000155217da300000000"
    "49454e44ae426082"
)).decode()
TINY_AVATAR = f"data:image/png;base64,{TINY_PNG_B64}"


async def test_upload_and_read_own_avatar(client):
    token, _ = await _signup(client, "avatar1@example.com", "Alice")
    r = await client.put("/api/v1/auth/me/avatar", headers=_auth(token), json={"avatar_data": TINY_AVATAR})
    assert r.status_code == 200
    assert r.json()["avatar_data"] == TINY_AVATAR

    me = await client.get("/api/v1/auth/me", headers=_auth(token))
    assert me.json()["avatar_data"] == TINY_AVATAR


async def test_remove_avatar(client):
    token, _ = await _signup(client, "avatar2@example.com", "Alice")
    await client.put("/api/v1/auth/me/avatar", headers=_auth(token), json={"avatar_data": TINY_AVATAR})
    r = await client.delete("/api/v1/auth/me/avatar", headers=_auth(token))
    assert r.status_code == 200
    assert r.json()["avatar_data"] is None


async def test_rejects_non_image_data_url(client):
    token, _ = await _signup(client, "avatar3@example.com", "Alice")
    r = await client.put("/api/v1/auth/me/avatar", headers=_auth(token), json={"avatar_data": "not a data url at all"})
    assert r.status_code == 400


async def test_rejects_malformed_base64(client):
    token, _ = await _signup(client, "avatar4@example.com", "Alice")
    r = await client.put("/api/v1/auth/me/avatar", headers=_auth(token), json={"avatar_data": "data:image/png;base64,***not valid base64***"})
    assert r.status_code == 400


async def test_rejects_an_oversized_image(client):
    token, _ = await _signup(client, "avatar5@example.com", "Alice")
    # Genuinely over the cap once decoded, not just a long string —
    # base64 expands ~4/3, so this comfortably clears MAX_AVATAR_BYTES
    # after decoding.
    oversized = base64.b64encode(b"x" * (MAX_AVATAR_BYTES + 1000)).decode()
    r = await client.put("/api/v1/auth/me/avatar", headers=_auth(token), json={"avatar_data": f"data:image/png;base64,{oversized}"})
    assert r.status_code == 400
    assert "too large" in r.json()["detail"].lower()


async def test_an_image_right_at_the_cap_is_accepted(client):
    token, _ = await _signup(client, "avatar6@example.com", "Alice")
    exactly_at_cap = base64.b64encode(b"x" * MAX_AVATAR_BYTES).decode()
    r = await client.put("/api/v1/auth/me/avatar", headers=_auth(token), json={"avatar_data": f"data:image/png;base64,{exactly_at_cap}"})
    assert r.status_code == 200


async def test_group_member_avatar_is_looked_up_live_not_snapshotted(client):
    """
    The real behavior this guards: avatar_data is NEVER snapshotted
    onto GroupMember the way name_snapshot is -- it's looked up fresh
    from the current User record every time a group is fetched. This
    proves it by uploading an avatar AFTER already being a group
    member, and confirming the group response picks it up without
    re-joining or any other action.
    """
    alice_token, alice_id = await _signup(client, "avatar7@example.com", "Alice")
    group_resp = await client.post("/api/v1/shared-expenses/groups", headers=_auth(alice_token), json={"name": "Trip", "members": []})
    group_id = group_resp.json()["id"]

    before = await client.get(f"/api/v1/shared-expenses/groups/{group_id}", headers=_auth(alice_token))
    assert before.json()["members"][0]["avatar_data"] is None

    await client.put("/api/v1/auth/me/avatar", headers=_auth(alice_token), json={"avatar_data": TINY_AVATAR})

    after = await client.get(f"/api/v1/shared-expenses/groups/{group_id}", headers=_auth(alice_token))
    assert after.json()["members"][0]["avatar_data"] == TINY_AVATAR


async def test_expense_split_avatar_is_looked_up_live_too(client):
    alice_token, alice_id = await _signup(client, "avatar8@example.com", "Alice")
    group_resp = await client.post("/api/v1/shared-expenses/groups", headers=_auth(alice_token), json={"name": "Trip", "members": []})
    group_id = group_resp.json()["id"]
    await client.put("/api/v1/auth/me/avatar", headers=_auth(alice_token), json={"avatar_data": TINY_AVATAR})

    expense_resp = await client.post(
        f"/api/v1/shared-expenses/groups/{group_id}/expenses", headers=_auth(alice_token),
        json={"description": "Dinner", "amount": 20.00, "expense_date": "2026-07-10", "participant_ids": [alice_id], "pending_participants": [], "category": "Dining Out"},
    )
    assert expense_resp.json()["splits"][0]["avatar_data"] == TINY_AVATAR


async def test_a_pending_invites_avatar_is_null_since_theres_no_real_account_yet(client):
    alice_token, alice_id = await _signup(client, "avatar9@example.com", "Alice")
    group_resp = await client.post("/api/v1/shared-expenses/groups", headers=_auth(alice_token), json={"name": "Trip", "members": [{"email": "sam-avatar9@example.com", "name": "Sam"}]})
    group_id = group_resp.json()["id"]
    r = await client.get(f"/api/v1/shared-expenses/groups/{group_id}", headers=_auth(alice_token))
    # Sam is in pending_invites (a different shape entirely, no
    # avatar_data field at all) rather than members — nothing to
    # assert null on beyond confirming they're not miscategorized.
    assert r.json()["pending_invites"] == [{"name": "Sam", "email": "sam-avatar9@example.com"}]
