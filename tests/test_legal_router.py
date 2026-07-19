async def _signup(client, email, name):
    r = await client.post("/api/v1/auth/signup", json={"email": email, "password": "hunter2222", "display_name": name})
    body = r.json()
    return body["access_token"], body["user"]["id"]


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


async def test_create_and_list_records(client):
    token, _ = await _signup(client, "alice-lr1@example.com", "Alice")
    r = await client.post("/api/v1/legal/records", headers=_auth(token), json={"record_type": "will", "title": "My will", "status": "active"})
    assert r.status_code == 201
    record_id = r.json()["id"]
    assert r.json()["record_type"] == "will"

    r2 = await client.get("/api/v1/legal/records", headers=_auth(token))
    assert r2.status_code == 200
    assert len(r2.json()) == 1
    assert r2.json()[0]["id"] == record_id


async def test_create_record_rejects_an_invalid_record_type(client):
    token, _ = await _signup(client, "alice-lr2@example.com", "Alice")
    r = await client.post("/api/v1/legal/records", headers=_auth(token), json={"record_type": "not-real", "title": "Test"})
    assert r.status_code == 400


async def test_create_record_rejects_an_empty_title(client):
    token, _ = await _signup(client, "alice-lr3@example.com", "Alice")
    r = await client.post("/api/v1/legal/records", headers=_auth(token), json={"record_type": "will", "title": ""})
    assert r.status_code == 422


async def test_create_record_rejects_a_negative_amount(client):
    token, _ = await _signup(client, "alice-lr4@example.com", "Alice")
    r = await client.post("/api/v1/legal/records", headers=_auth(token), json={"record_type": "insurance_claim", "title": "Claim", "amount": -500})
    assert r.status_code == 422


async def test_legal_records_require_authentication(client):
    r = await client.get("/api/v1/legal/records")
    assert r.status_code in (401, 403)


async def test_list_records_filters_by_type_via_query_param(client):
    token, _ = await _signup(client, "alice-lr5@example.com", "Alice")
    await client.post("/api/v1/legal/records", headers=_auth(token), json={"record_type": "will", "title": "Will"})
    await client.post("/api/v1/legal/records", headers=_auth(token), json={"record_type": "contract", "title": "Lease"})

    r = await client.get("/api/v1/legal/records?record_type=will", headers=_auth(token))
    assert len(r.json()) == 1
    assert r.json()[0]["title"] == "Will"


async def test_records_are_isolated_between_users(client):
    alice_token, _ = await _signup(client, "alice-lr6@example.com", "Alice")
    bob_token, _ = await _signup(client, "bob-lr6@example.com", "Bob")
    await client.post("/api/v1/legal/records", headers=_auth(alice_token), json={"record_type": "will", "title": "Alice's will"})

    r = await client.get("/api/v1/legal/records", headers=_auth(bob_token))
    assert r.json() == []


async def test_update_record_succeeds_for_the_owner(client):
    token, _ = await _signup(client, "alice-lr7@example.com", "Alice")
    r = await client.post("/api/v1/legal/records", headers=_auth(token), json={"record_type": "contract", "title": "Lease v1"})
    record_id = r.json()["id"]

    r2 = await client.put(f"/api/v1/legal/records/{record_id}", headers=_auth(token), json={"title": "Lease v2", "status": "renewed"})
    assert r2.status_code == 200
    assert r2.json()["title"] == "Lease v2"
    assert r2.json()["status"] == "renewed"


async def test_update_record_404s_for_someone_elses_record(client):
    alice_token, _ = await _signup(client, "alice-lr8@example.com", "Alice")
    bob_token, _ = await _signup(client, "bob-lr8@example.com", "Bob")
    r = await client.post("/api/v1/legal/records", headers=_auth(bob_token), json={"record_type": "will", "title": "Bob's will"})
    bob_record_id = r.json()["id"]

    r2 = await client.put(f"/api/v1/legal/records/{bob_record_id}", headers=_auth(alice_token), json={"title": "Hijacked"})
    assert r2.status_code == 404


async def test_delete_record_succeeds_for_the_owner(client):
    token, _ = await _signup(client, "alice-lr9@example.com", "Alice")
    r = await client.post("/api/v1/legal/records", headers=_auth(token), json={"record_type": "will", "title": "My will"})
    record_id = r.json()["id"]

    r2 = await client.delete(f"/api/v1/legal/records/{record_id}", headers=_auth(token))
    assert r2.status_code == 204

    r3 = await client.get("/api/v1/legal/records", headers=_auth(token))
    assert r3.json() == []


async def test_delete_record_404s_for_someone_elses_record(client):
    alice_token, _ = await _signup(client, "alice-lr10@example.com", "Alice")
    bob_token, _ = await _signup(client, "bob-lr10@example.com", "Bob")
    r = await client.post("/api/v1/legal/records", headers=_auth(bob_token), json={"record_type": "will", "title": "Bob's will"})
    bob_record_id = r.json()["id"]

    r2 = await client.delete(f"/api/v1/legal/records/{bob_record_id}", headers=_auth(alice_token))
    assert r2.status_code == 404


async def test_delete_record_404s_for_a_nonexistent_id(client):
    token, _ = await _signup(client, "alice-lr11@example.com", "Alice")
    r = await client.delete("/api/v1/legal/records/does-not-exist", headers=_auth(token))
    assert r.status_code == 404
