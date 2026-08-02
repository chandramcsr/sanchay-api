async def _create_account(client, clerk_auth, user="user_alice", starting_balance=0.0):
    r = await client.post(
        "/api/v1/accounts",
        headers=clerk_auth(user),
        json={"name": "Test Account", "type": "checking", "starting_balance": starting_balance},
    )
    return r.json()["id"]


async def test_create_transaction(client, clerk_auth):
    account_id = await _create_account(client, clerk_auth)
    r = await client.post(
        "/api/v1/transactions",
        headers=clerk_auth("user_alice"),
        json={"account_id": account_id, "amount": -25.5, "description": "Lunch", "date": "2026-08-01"},
    )
    assert r.status_code == 201
    assert r.json()["amount"] == -25.5


async def test_create_transaction_rejects_unowned_account(client, clerk_auth):
    """Posting a transaction against an account that isn't the caller's
    -- the account ownership check in transaction_service._owns_account,
    not just trusting the account_id in the request body."""
    account_id = await _create_account(client, clerk_auth, user="user_alice")
    r = await client.post(
        "/api/v1/transactions",
        headers=clerk_auth("user_bob"),
        json={"account_id": account_id, "amount": 100.0, "description": "Not mine", "date": "2026-08-01"},
    )
    assert r.status_code == 404


async def test_list_transactions_filters_by_account_and_date(client, clerk_auth):
    account_id = await _create_account(client, clerk_auth)
    other_account_id = await _create_account(client, clerk_auth)
    await client.post(
        "/api/v1/transactions",
        headers=clerk_auth("user_alice"),
        json={"account_id": account_id, "amount": -10, "description": "In range", "date": "2026-08-15"},
    )
    await client.post(
        "/api/v1/transactions",
        headers=clerk_auth("user_alice"),
        json={"account_id": account_id, "amount": -20, "description": "Out of range", "date": "2026-01-01"},
    )
    await client.post(
        "/api/v1/transactions",
        headers=clerk_auth("user_alice"),
        json={"account_id": other_account_id, "amount": -30, "description": "Wrong account", "date": "2026-08-15"},
    )

    r = await client.get(
        "/api/v1/transactions",
        headers=clerk_auth("user_alice"),
        params={"account_id": account_id, "start_date": "2026-08-01", "end_date": "2026-08-31"},
    )
    assert r.status_code == 200
    results = r.json()
    assert len(results) == 1
    assert results[0]["description"] == "In range"


async def test_ownership_isolation_on_update_and_delete(client, clerk_auth):
    account_id = await _create_account(client, clerk_auth, user="user_alice")
    r = await client.post(
        "/api/v1/transactions",
        headers=clerk_auth("user_alice"),
        json={"account_id": account_id, "amount": -5, "description": "Alice's", "date": "2026-08-01"},
    )
    transaction_id = r.json()["id"]

    r2 = await client.put(
        f"/api/v1/transactions/{transaction_id}",
        headers=clerk_auth("user_bob"),
        json={"description": "Hijacked"},
    )
    assert r2.status_code == 404

    r3 = await client.delete(f"/api/v1/transactions/{transaction_id}", headers=clerk_auth("user_bob"))
    assert r3.status_code == 404
