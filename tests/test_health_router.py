async def _signup(client, email, name):
    r = await client.post("/api/v1/auth/signup", json={"email": email, "password": "hunter2222", "display_name": name})
    body = r.json()
    return body["access_token"], body["user"]["id"]


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


async def test_get_profile_returns_null_before_anything_is_saved(client):
    token, _ = await _signup(client, "alice-hr1@example.com", "Alice")
    r = await client.get("/api/v1/health/profile", headers=_auth(token))
    assert r.status_code == 200
    assert r.json() is None


async def test_put_profile_creates_then_get_returns_it(client):
    token, _ = await _signup(client, "alice-hr2@example.com", "Alice")
    r = await client.put(
        "/api/v1/health/profile", headers=_auth(token),
        json={"height_cm": 170.0, "age": 35, "biological_sex": "female", "notes": "No known allergies"},
    )
    assert r.status_code == 200
    assert r.json()["height_cm"] == 170.0

    r2 = await client.get("/api/v1/health/profile", headers=_auth(token))
    assert r2.json()["height_cm"] == 170.0
    assert r2.json()["notes"] == "No known allergies"


async def test_put_profile_rejects_an_invalid_biological_sex(client):
    token, _ = await _signup(client, "alice-hr3@example.com", "Alice")
    r = await client.put(
        "/api/v1/health/profile", headers=_auth(token),
        json={"height_cm": 170.0, "biological_sex": "not-a-real-option"},
    )
    assert r.status_code == 400


async def test_put_profile_rejects_a_non_positive_height(client):
    token, _ = await _signup(client, "alice-hr4@example.com", "Alice")
    r = await client.put("/api/v1/health/profile", headers=_auth(token), json={"height_cm": -5})
    assert r.status_code == 422


async def test_health_profile_requires_authentication(client):
    r = await client.get("/api/v1/health/profile")
    assert r.status_code in (401, 403)


async def test_add_and_list_weight_entries(client):
    token, _ = await _signup(client, "alice-hr5@example.com", "Alice")
    r = await client.post("/api/v1/health/weight", headers=_auth(token), json={"weight_kg": 70.0, "recorded_date": "2026-07-01"})
    assert r.status_code == 201
    entry_id = r.json()["id"]

    r2 = await client.get("/api/v1/health/weight", headers=_auth(token))
    assert r2.status_code == 200
    assert len(r2.json()) == 1
    assert r2.json()[0]["id"] == entry_id


async def test_weight_entries_are_isolated_between_users(client):
    alice_token, _ = await _signup(client, "alice-hr6@example.com", "Alice")
    bob_token, _ = await _signup(client, "bob-hr6@example.com", "Bob")
    await client.post("/api/v1/health/weight", headers=_auth(alice_token), json={"weight_kg": 70.0, "recorded_date": "2026-07-01"})

    r = await client.get("/api/v1/health/weight", headers=_auth(bob_token))
    assert r.json() == []


async def test_delete_weight_entry_succeeds_for_the_owner(client):
    token, _ = await _signup(client, "alice-hr7@example.com", "Alice")
    r = await client.post("/api/v1/health/weight", headers=_auth(token), json={"weight_kg": 70.0, "recorded_date": "2026-07-01"})
    entry_id = r.json()["id"]

    r2 = await client.delete(f"/api/v1/health/weight/{entry_id}", headers=_auth(token))
    assert r2.status_code == 204

    r3 = await client.get("/api/v1/health/weight", headers=_auth(token))
    assert r3.json() == []


async def test_delete_weight_entry_404s_for_someone_elses_entry(client):
    alice_token, _ = await _signup(client, "alice-hr8@example.com", "Alice")
    bob_token, _ = await _signup(client, "bob-hr8@example.com", "Bob")
    r = await client.post("/api/v1/health/weight", headers=_auth(bob_token), json={"weight_kg": 80.0, "recorded_date": "2026-07-01"})
    bob_entry_id = r.json()["id"]

    r2 = await client.delete(f"/api/v1/health/weight/{bob_entry_id}", headers=_auth(alice_token))
    assert r2.status_code == 404

    # Bob's entry is untouched.
    r3 = await client.get("/api/v1/health/weight", headers=_auth(bob_token))
    assert len(r3.json()) == 1


async def test_delete_weight_entry_404s_for_a_nonexistent_id(client):
    token, _ = await _signup(client, "alice-hr9@example.com", "Alice")
    r = await client.delete("/api/v1/health/weight/does-not-exist", headers=_auth(token))
    assert r.status_code == 404


async def test_add_and_list_blood_pressure_entries(client):
    token, _ = await _signup(client, "alice-hr10@example.com", "Alice")
    r = await client.post("/api/v1/health/blood-pressure", headers=_auth(token), json={"systolic": 120, "diastolic": 80, "pulse": 70, "recorded_date": "2026-07-01"})
    assert r.status_code == 201
    entry_id = r.json()["id"]
    assert r.json()["systolic"] == 120
    assert r.json()["pulse"] == 70

    r2 = await client.get("/api/v1/health/blood-pressure", headers=_auth(token))
    assert r2.status_code == 200
    assert len(r2.json()) == 1
    assert r2.json()[0]["id"] == entry_id


async def test_add_blood_pressure_entry_without_pulse(client):
    token, _ = await _signup(client, "alice-hr11@example.com", "Alice")
    r = await client.post("/api/v1/health/blood-pressure", headers=_auth(token), json={"systolic": 120, "diastolic": 80, "recorded_date": "2026-07-01"})
    assert r.status_code == 201
    assert r.json()["pulse"] is None


async def test_blood_pressure_rejects_an_obviously_wrong_systolic(client):
    token, _ = await _signup(client, "alice-hr12@example.com", "Alice")
    r = await client.post("/api/v1/health/blood-pressure", headers=_auth(token), json={"systolic": 9999, "diastolic": 80, "recorded_date": "2026-07-01"})
    assert r.status_code == 422


async def test_blood_pressure_entries_are_isolated_between_users(client):
    alice_token, _ = await _signup(client, "alice-hr13@example.com", "Alice")
    bob_token, _ = await _signup(client, "bob-hr13@example.com", "Bob")
    await client.post("/api/v1/health/blood-pressure", headers=_auth(alice_token), json={"systolic": 120, "diastolic": 80, "recorded_date": "2026-07-01"})

    r = await client.get("/api/v1/health/blood-pressure", headers=_auth(bob_token))
    assert r.json() == []


async def test_delete_blood_pressure_entry_succeeds_for_the_owner(client):
    token, _ = await _signup(client, "alice-hr14@example.com", "Alice")
    r = await client.post("/api/v1/health/blood-pressure", headers=_auth(token), json={"systolic": 120, "diastolic": 80, "recorded_date": "2026-07-01"})
    entry_id = r.json()["id"]

    r2 = await client.delete(f"/api/v1/health/blood-pressure/{entry_id}", headers=_auth(token))
    assert r2.status_code == 204

    r3 = await client.get("/api/v1/health/blood-pressure", headers=_auth(token))
    assert r3.json() == []


async def test_delete_blood_pressure_entry_404s_for_someone_elses_entry(client):
    alice_token, _ = await _signup(client, "alice-hr15@example.com", "Alice")
    bob_token, _ = await _signup(client, "bob-hr15@example.com", "Bob")
    r = await client.post("/api/v1/health/blood-pressure", headers=_auth(bob_token), json={"systolic": 130, "diastolic": 85, "recorded_date": "2026-07-01"})
    bob_entry_id = r.json()["id"]

    r2 = await client.delete(f"/api/v1/health/blood-pressure/{bob_entry_id}", headers=_auth(alice_token))
    assert r2.status_code == 404
