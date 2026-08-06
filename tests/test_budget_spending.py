from datetime import date


async def _create_account(client, clerk_auth, user="user_alice"):
    r = await client.post(
        "/api/v1/accounts",
        headers=clerk_auth(user),
        json={"name": "Test Account", "type": "checking", "starting_balance": 0.0},
    )
    return r.json()["id"]


async def test_budget_spent_reflects_this_month_expenses(client, clerk_auth):
    account_id = await _create_account(client, clerk_auth)
    await client.put(
        "/api/v1/budgets", headers=clerk_auth("user_alice"), json={"category": "Groceries", "monthly_limit": 400.0}
    )

    today = date.today().isoformat()
    await client.post(
        "/api/v1/transactions",
        headers=clerk_auth("user_alice"),
        json={"account_id": account_id, "amount": -50, "description": "Store A", "category": "Groceries", "date": today},
    )
    await client.post(
        "/api/v1/transactions",
        headers=clerk_auth("user_alice"),
        json={"account_id": account_id, "amount": -30, "description": "Store B", "category": "Groceries", "date": today},
    )
    # Different category -- should NOT count toward Groceries spending
    await client.post(
        "/api/v1/transactions",
        headers=clerk_auth("user_alice"),
        json={"account_id": account_id, "amount": -1000, "description": "Rent", "category": "Housing", "date": today},
    )
    # Income in the Groceries category (unlikely in practice, but a real
    # edge case) -- must not count as spending, since it's not an expense.
    await client.post(
        "/api/v1/transactions",
        headers=clerk_auth("user_alice"),
        json={"account_id": account_id, "amount": 20, "description": "Grocery refund", "category": "Groceries", "date": today},
    )

    r = await client.get("/api/v1/budgets", headers=clerk_auth("user_alice"))
    groceries = next(b for b in r.json() if b["category"] == "Groceries")
    assert groceries["spent"] == 80.0  # 50 + 30, not the Housing or income rows


async def test_budget_spent_excludes_other_months(client, clerk_auth):
    account_id = await _create_account(client, clerk_auth)
    await client.put(
        "/api/v1/budgets", headers=clerk_auth("user_alice"), json={"category": "Groceries", "monthly_limit": 400.0}
    )
    await client.post(
        "/api/v1/transactions",
        headers=clerk_auth("user_alice"),
        json={"account_id": account_id, "amount": -50, "description": "Old", "category": "Groceries", "date": "2020-01-15"},
    )

    r = await client.get("/api/v1/budgets", headers=clerk_auth("user_alice"))
    groceries = next(b for b in r.json() if b["category"] == "Groceries")
    assert groceries["spent"] == 0.0


async def test_new_budget_has_zero_spent(client, clerk_auth):
    r = await client.put(
        "/api/v1/budgets", headers=clerk_auth("user_alice"), json={"category": "Fun", "monthly_limit": 100.0}
    )
    assert r.json()["spent"] == 0.0
