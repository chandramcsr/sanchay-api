async def test_upsert_creates_then_updates_same_category(client, clerk_auth):
    """Setting a limit for a category twice should update the existing
    row, not create a second one alongside it -- the actual behavior
    the unique index + upsert logic exist to guarantee."""
    r1 = await client.put(
        "/api/v1/budgets", headers=clerk_auth("user_alice"), json={"category": "Groceries", "monthly_limit": 400.0}
    )
    assert r1.status_code == 200
    budget_id = r1.json()["id"]

    r2 = await client.put(
        "/api/v1/budgets", headers=clerk_auth("user_alice"), json={"category": "Groceries", "monthly_limit": 500.0}
    )
    assert r2.status_code == 200
    assert r2.json()["id"] == budget_id  # same row, not a new one
    assert r2.json()["monthly_limit"] == 500.0

    r3 = await client.get("/api/v1/budgets", headers=clerk_auth("user_alice"))
    assert len(r3.json()) == 1


async def test_ownership_isolation(client, clerk_auth):
    await client.put(
        "/api/v1/budgets", headers=clerk_auth("user_alice"), json={"category": "Rent", "monthly_limit": 2000.0}
    )
    r = await client.get("/api/v1/budgets", headers=clerk_auth("user_bob"))
    assert r.json() == []


async def test_delete_budget(client, clerk_auth):
    r1 = await client.put(
        "/api/v1/budgets", headers=clerk_auth("user_alice"), json={"category": "Fun", "monthly_limit": 100.0}
    )
    budget_id = r1.json()["id"]

    r2 = await client.delete(f"/api/v1/budgets/{budget_id}", headers=clerk_auth("user_alice"))
    assert r2.status_code == 204

    r3 = await client.get("/api/v1/budgets", headers=clerk_auth("user_alice"))
    assert r3.json() == []


async def test_delete_rejects_unowned_budget(client, clerk_auth):
    r1 = await client.put(
        "/api/v1/budgets", headers=clerk_auth("user_alice"), json={"category": "Fun", "monthly_limit": 100.0}
    )
    budget_id = r1.json()["id"]

    r2 = await client.delete(f"/api/v1/budgets/{budget_id}", headers=clerk_auth("user_bob"))
    assert r2.status_code == 404
