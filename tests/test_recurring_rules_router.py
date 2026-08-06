from datetime import date, timedelta


async def _create_account(client, clerk_auth, user="user_alice"):
    r = await client.post(
        "/api/v1/accounts",
        headers=clerk_auth(user),
        json={"name": "Test Account", "type": "checking", "starting_balance": 0.0},
    )
    return r.json()["id"]


async def test_create_recurring_rule(client, clerk_auth):
    account_id = await _create_account(client, clerk_auth)
    r = await client.post(
        "/api/v1/recurring-rules",
        headers=clerk_auth("user_alice"),
        json={
            "account_id": account_id,
            "amount": -1500,
            "description": "Rent",
            "category": "Housing",
            "frequency": "monthly",
            "start_date": "2020-01-01",
        },
    )
    assert r.status_code == 201
    assert r.json()["last_materialized"] is None  # nothing materialized yet


async def test_create_recurring_rule_without_description_succeeds(client, clerk_auth):
    """Same real bug as transactions, same fix -- description is
    optional (the frontend labels it 'Note (optional)'), not
    required with min_length=1."""
    account_id = await _create_account(client, clerk_auth)
    r = await client.post(
        "/api/v1/recurring-rules",
        headers=clerk_auth("user_alice"),
        json={
            "account_id": account_id,
            "amount": 5000,
            "category": "Salary",
            "frequency": "monthly",
            "start_date": "2026-08-01",
        },
    )
    assert r.status_code == 201
    assert r.json()["description"] is None


async def test_create_rejects_invalid_frequency(client, clerk_auth):
    account_id = await _create_account(client, clerk_auth)
    r = await client.post(
        "/api/v1/recurring-rules",
        headers=clerk_auth("user_alice"),
        json={
            "account_id": account_id,
            "amount": -10,
            "description": "Bad",
            "frequency": "daily",  # not a supported frequency
            "start_date": "2020-01-01",
        },
    )
    assert r.status_code == 400


async def test_create_rejects_unowned_account(client, clerk_auth):
    account_id = await _create_account(client, clerk_auth, user="user_alice")
    r = await client.post(
        "/api/v1/recurring-rules",
        headers=clerk_auth("user_bob"),
        json={
            "account_id": account_id,
            "amount": -10,
            "description": "Not mine",
            "frequency": "monthly",
            "start_date": "2020-01-01",
        },
    )
    assert r.status_code == 404


async def test_materialization_catches_up_missed_occurrences(client, clerk_auth):
    """The real point of this feature: a monthly rule anchored years in
    the past should generate every occurrence due since, with correct
    historical dates -- not just one, and not silently nothing."""
    account_id = await _create_account(client, clerk_auth)
    await client.post(
        "/api/v1/recurring-rules",
        headers=clerk_auth("user_alice"),
        json={
            "account_id": account_id,
            "amount": -1500,
            "description": "Rent",
            "category": "Housing",
            "frequency": "monthly",
            "start_date": "2020-01-01",
        },
    )

    # GET /accounts triggers materialization as a side effect
    await client.get("/api/v1/accounts", headers=clerk_auth("user_alice"))

    r = await client.get(
        "/api/v1/transactions",
        headers=clerk_auth("user_alice"),
        params={"account_id": account_id, "limit": 500},
    )
    transactions = r.json()
    assert len(transactions) > 12  # more than a year of monthly rent since 2020
    assert all(t["description"] == "Rent" for t in transactions)
    assert all(t["amount"] == -1500.0 for t in transactions)


async def test_materialization_is_idempotent(client, clerk_auth):
    """Calling materialization twice in a row with nothing new due
    should not create duplicate transactions."""
    account_id = await _create_account(client, clerk_auth)
    await client.post(
        "/api/v1/recurring-rules",
        headers=clerk_auth("user_alice"),
        json={
            "account_id": account_id,
            "amount": -50,
            "description": "Subscription",
            "frequency": "monthly",
            "start_date": (date.today() - timedelta(days=1)).isoformat(),
        },
    )

    await client.get("/api/v1/accounts", headers=clerk_auth("user_alice"))
    r1 = await client.get(
        "/api/v1/transactions", headers=clerk_auth("user_alice"), params={"account_id": account_id}
    )
    count_after_first = len(r1.json())

    await client.get("/api/v1/accounts", headers=clerk_auth("user_alice"))
    r2 = await client.get(
        "/api/v1/transactions", headers=clerk_auth("user_alice"), params={"account_id": account_id}
    )
    count_after_second = len(r2.json())

    assert count_after_first == count_after_second == 1


async def test_ownership_isolation(client, clerk_auth):
    account_id = await _create_account(client, clerk_auth, user="user_alice")
    r = await client.post(
        "/api/v1/recurring-rules",
        headers=clerk_auth("user_alice"),
        json={
            "account_id": account_id,
            "amount": -10,
            "description": "Alice's",
            "frequency": "weekly",
            "start_date": "2026-01-01",
        },
    )
    rule_id = r.json()["id"]

    r2 = await client.put(
        f"/api/v1/recurring-rules/{rule_id}", headers=clerk_auth("user_bob"), json={"description": "Hijacked"}
    )
    assert r2.status_code == 404

    r3 = await client.delete(f"/api/v1/recurring-rules/{rule_id}", headers=clerk_auth("user_bob"))
    assert r3.status_code == 404


async def test_delete_account_cascades_to_recurring_rules(client, clerk_auth, sanchay_app_db_session):
    from sqlalchemy import select

    from app.models.recurring_rule import RecurringRule

    account_id = await _create_account(client, clerk_auth)
    await client.post(
        "/api/v1/recurring-rules",
        headers=clerk_auth("user_alice"),
        json={
            "account_id": account_id,
            "amount": -10,
            "description": "Temp rule",
            "frequency": "monthly",
            "start_date": "2026-01-01",
        },
    )

    await client.delete(f"/api/v1/accounts/{account_id}", headers=clerk_auth("user_alice"))

    result = await sanchay_app_db_session.execute(
        select(RecurringRule).where(RecurringRule.account_id == account_id)
    )
    assert result.scalar_one_or_none() is None
