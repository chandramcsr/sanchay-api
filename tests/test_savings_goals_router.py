from datetime import date, timedelta


async def _create_account(client, clerk_auth, user="user_alice", starting_balance=0.0):
    r = await client.post(
        "/api/v1/accounts",
        headers=clerk_auth(user),
        json={"name": "Savings", "type": "savings", "starting_balance": starting_balance},
    )
    return r.json()["id"]


async def test_create_savings_goal(client, clerk_auth):
    account_id = await _create_account(client, clerk_auth, starting_balance=500)
    r = await client.post(
        "/api/v1/savings-goals",
        headers=clerk_auth("user_alice"),
        json={"name": "Emergency Fund", "target_amount": 3000, "account_id": account_id},
    )
    assert r.status_code == 201
    body = r.json()
    assert body["current_amount"] == 500.0
    assert round(body["pct"], 2) == round(500 / 3000 * 100, 2)
    assert body["remaining"] == 2500.0


async def test_create_rejects_unowned_account(client, clerk_auth):
    account_id = await _create_account(client, clerk_auth, user="user_alice")
    r = await client.post(
        "/api/v1/savings-goals",
        headers=clerk_auth("user_bob"),
        json={"name": "Not mine", "target_amount": 100, "account_id": account_id},
    )
    assert r.status_code == 404


async def test_progress_reflects_account_balance_not_a_separate_ledger(client, clerk_auth):
    """The core design property: a goal's progress IS the linked
    account's balance -- logging a transaction against that account
    should move the goal's progress without touching the goal itself."""
    account_id = await _create_account(client, clerk_auth, starting_balance=0)
    r = await client.post(
        "/api/v1/savings-goals",
        headers=clerk_auth("user_alice"),
        json={"name": "Travel", "target_amount": 1000, "account_id": account_id},
    )
    goal_id = r.json()["id"]
    assert r.json()["current_amount"] == 0.0

    await client.post(
        "/api/v1/transactions",
        headers=clerk_auth("user_alice"),
        json={"account_id": account_id, "amount": 400, "description": "Deposit", "date": date.today().isoformat()},
    )

    r2 = await client.get("/api/v1/savings-goals", headers=clerk_auth("user_alice"))
    goal = next(g for g in r2.json() if g["id"] == goal_id)
    assert goal["current_amount"] == 400.0
    assert goal["remaining"] == 600.0


async def test_goal_reached_has_zero_remaining_and_full_pct(client, clerk_auth):
    account_id = await _create_account(client, clerk_auth, starting_balance=1000)
    r = await client.post(
        "/api/v1/savings-goals",
        headers=clerk_auth("user_alice"),
        json={"name": "Reached", "target_amount": 500, "account_id": account_id},
    )
    body = r.json()
    assert body["remaining"] == 0.0
    assert body["pct"] == 100.0  # capped at 100, not 200
    assert body["projected_completion_date"] == date.today().isoformat()


async def test_projected_completion_date_uses_recent_contribution_rate(client, clerk_auth):
    """A real deposit within the last 90 days should produce a
    concrete projection date, not None -- confirms the trailing-90-day
    rate calculation actually picks up real transaction history."""
    account_id = await _create_account(client, clerk_auth, starting_balance=0)
    r = await client.post(
        "/api/v1/savings-goals",
        headers=clerk_auth("user_alice"),
        json={"name": "Down Payment", "target_amount": 10000, "account_id": account_id},
    )
    goal_id = r.json()["id"]

    await client.post(
        "/api/v1/transactions",
        headers=clerk_auth("user_alice"),
        json={
            "account_id": account_id,
            "amount": 3000,
            "description": "Deposit",
            "date": (date.today() - timedelta(days=10)).isoformat(),
        },
    )

    r2 = await client.get("/api/v1/savings-goals", headers=clerk_auth("user_alice"))
    goal = next(g for g in r2.json() if g["id"] == goal_id)
    assert goal["monthly_contribution_rate"] > 0
    assert goal["projected_completion_date"] is not None


async def test_no_recent_contributions_gives_no_projection(client, clerk_auth):
    account_id = await _create_account(client, clerk_auth, starting_balance=100)
    r = await client.post(
        "/api/v1/savings-goals",
        headers=clerk_auth("user_alice"),
        json={"name": "Stalled", "target_amount": 5000, "account_id": account_id},
    )
    body = r.json()
    assert body["monthly_contribution_rate"] == 0.0
    assert body["projected_completion_date"] is None
    assert body["on_track_for_target_date"] is None  # no target_date was set


async def test_on_track_for_target_date_false_when_behind(client, clerk_auth):
    account_id = await _create_account(client, clerk_auth, starting_balance=0)
    far_future_target = (date.today() + timedelta(days=3650)).isoformat()  # 10 years out is irrelevant here
    r = await client.post(
        "/api/v1/savings-goals",
        headers=clerk_auth("user_alice"),
        json={
            "name": "No progress",
            "target_amount": 5000,
            "target_date": far_future_target,
            "account_id": account_id,
        },
    )
    # No contributions at all -- no projection possible, so explicitly not on track
    assert r.json()["on_track_for_target_date"] is False


async def test_update_can_repoint_to_a_different_account(client, clerk_auth):
    """Unlike transactions/recurring rules, a goal's account_id IS
    editable -- matches ledger-app's own GoalModal."""
    account_a = await _create_account(client, clerk_auth, starting_balance=100)
    account_b = await _create_account(client, clerk_auth, starting_balance=900)
    r = await client.post(
        "/api/v1/savings-goals",
        headers=clerk_auth("user_alice"),
        json={"name": "Movable", "target_amount": 1000, "account_id": account_a},
    )
    goal_id = r.json()["id"]
    assert r.json()["current_amount"] == 100.0

    r2 = await client.put(
        f"/api/v1/savings-goals/{goal_id}", headers=clerk_auth("user_alice"), json={"account_id": account_b}
    )
    assert r2.status_code == 200
    assert r2.json()["account_id"] == account_b
    assert r2.json()["current_amount"] == 900.0


async def test_update_rejects_repointing_to_unowned_account(client, clerk_auth):
    account_a = await _create_account(client, clerk_auth, user="user_alice")
    account_b = await _create_account(client, clerk_auth, user="user_bob")
    r = await client.post(
        "/api/v1/savings-goals",
        headers=clerk_auth("user_alice"),
        json={"name": "Mine", "target_amount": 1000, "account_id": account_a},
    )
    goal_id = r.json()["id"]

    r2 = await client.put(
        f"/api/v1/savings-goals/{goal_id}", headers=clerk_auth("user_alice"), json={"account_id": account_b}
    )
    assert r2.status_code == 404


async def test_ownership_isolation_on_list_and_delete(client, clerk_auth):
    account_id = await _create_account(client, clerk_auth, user="user_alice")
    r = await client.post(
        "/api/v1/savings-goals",
        headers=clerk_auth("user_alice"),
        json={"name": "Alice's", "target_amount": 1000, "account_id": account_id},
    )
    goal_id = r.json()["id"]

    r2 = await client.get("/api/v1/savings-goals", headers=clerk_auth("user_bob"))
    assert r2.json() == []

    r3 = await client.delete(f"/api/v1/savings-goals/{goal_id}", headers=clerk_auth("user_bob"))
    assert r3.status_code == 404


async def test_delete_account_cascades_to_savings_goals(client, clerk_auth, sanchay_app_db_session):
    from sqlalchemy import select

    from app.models.savings_goal import SavingsGoal

    account_id = await _create_account(client, clerk_auth)
    await client.post(
        "/api/v1/savings-goals",
        headers=clerk_auth("user_alice"),
        json={"name": "Temp goal", "target_amount": 1000, "account_id": account_id},
    )

    await client.delete(f"/api/v1/accounts/{account_id}", headers=clerk_auth("user_alice"))

    result = await sanchay_app_db_session.execute(
        select(SavingsGoal).where(SavingsGoal.account_id == account_id)
    )
    assert result.scalar_one_or_none() is None
