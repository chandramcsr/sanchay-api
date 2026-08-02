async def test_create_and_list_account(client, clerk_auth):
    r = await client.post(
        "/api/v1/accounts",
        headers=clerk_auth("user_alice"),
        json={"name": "Chase Checking", "type": "checking", "starting_balance": 100.0},
    )
    assert r.status_code == 201
    body = r.json()
    assert body["name"] == "Chase Checking"
    assert body["current_balance"] == 100.0  # no transactions yet

    r2 = await client.get("/api/v1/accounts", headers=clerk_auth("user_alice"))
    assert r2.status_code == 200
    assert len(r2.json()) == 1


async def test_current_balance_reflects_transactions(client, clerk_auth):
    r = await client.post(
        "/api/v1/accounts",
        headers=clerk_auth("user_alice"),
        json={"name": "Savings", "type": "savings", "starting_balance": 500.0},
    )
    account_id = r.json()["id"]

    await client.post(
        "/api/v1/transactions",
        headers=clerk_auth("user_alice"),
        json={"account_id": account_id, "amount": -50.0, "description": "Groceries", "date": "2026-08-01"},
    )
    await client.post(
        "/api/v1/transactions",
        headers=clerk_auth("user_alice"),
        json={"account_id": account_id, "amount": 200.0, "description": "Paycheck", "date": "2026-08-02"},
    )

    r2 = await client.get("/api/v1/accounts", headers=clerk_auth("user_alice"))
    account = next(a for a in r2.json() if a["id"] == account_id)
    assert account["current_balance"] == 650.0  # 500 - 50 + 200


async def test_create_account_rejects_invalid_type(client, clerk_auth):
    r = await client.post(
        "/api/v1/accounts",
        headers=clerk_auth("user_alice"),
        json={"name": "Weird", "type": "not-a-real-type", "starting_balance": 0},
    )
    assert r.status_code == 400


async def test_requires_authentication(client):
    r = await client.get("/api/v1/accounts")
    assert r.status_code == 401


async def test_rejects_expired_token(client, clerk_auth):
    r = await client.get("/api/v1/accounts", headers=clerk_auth("user_alice", expired=True))
    assert r.status_code == 401


async def test_rejects_wrong_authorized_party(client, clerk_auth):
    """azp mismatch — a token issued for a different frontend origin
    than this deployment's configured CLERK_AUTHORIZED_PARTIES. This is
    the actual CSRF protection Clerk's own docs call out as important
    to enforce, not defense-in-depth theater — verifying it's real."""
    r = await client.get(
        "/api/v1/accounts", headers=clerk_auth("user_alice", azp="https://evil.example.com")
    )
    assert r.status_code == 401


async def test_ownership_isolation_on_list(client, clerk_auth):
    """User B should never see user A's accounts, even though both are
    genuinely, validly authenticated — this is the actual test that
    the clerk_user_id scoping in every query is doing its job."""
    await client.post(
        "/api/v1/accounts",
        headers=clerk_auth("user_alice"),
        json={"name": "Alice's account", "type": "checking", "starting_balance": 0},
    )
    r = await client.get("/api/v1/accounts", headers=clerk_auth("user_bob"))
    assert r.status_code == 200
    assert r.json() == []


async def test_ownership_isolation_on_update_and_delete(client, clerk_auth):
    """User B can't update or delete user A's account by guessing/
    reusing its id — this is what the WHERE clerk_user_id = ... clause
    in the service layer actually prevents, not just a UI-level hide."""
    r = await client.post(
        "/api/v1/accounts",
        headers=clerk_auth("user_alice"),
        json={"name": "Alice's account", "type": "checking", "starting_balance": 0},
    )
    account_id = r.json()["id"]

    r2 = await client.put(
        f"/api/v1/accounts/{account_id}", headers=clerk_auth("user_bob"), json={"name": "Hijacked"}
    )
    assert r2.status_code == 404

    r3 = await client.delete(f"/api/v1/accounts/{account_id}", headers=clerk_auth("user_bob"))
    assert r3.status_code == 404

    # Confirm it's genuinely untouched, not silently modified before the 404
    r4 = await client.get("/api/v1/accounts", headers=clerk_auth("user_alice"))
    assert r4.json()[0]["name"] == "Alice's account"


async def test_delete_account_cascades_to_transactions(client, clerk_auth, sanchay_app_db_session):
    from sqlalchemy import select

    from app.models.transaction import Transaction

    r = await client.post(
        "/api/v1/accounts",
        headers=clerk_auth("user_alice"),
        json={"name": "Temp", "type": "checking", "starting_balance": 0},
    )
    account_id = r.json()["id"]
    await client.post(
        "/api/v1/transactions",
        headers=clerk_auth("user_alice"),
        json={"account_id": account_id, "amount": -10.0, "description": "Coffee", "date": "2026-08-01"},
    )

    r2 = await client.delete(f"/api/v1/accounts/{account_id}", headers=clerk_auth("user_alice"))
    assert r2.status_code == 204

    result = await sanchay_app_db_session.execute(
        select(Transaction).where(Transaction.account_id == account_id)
    )
    assert result.scalar_one_or_none() is None
