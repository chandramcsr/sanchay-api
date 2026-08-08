from datetime import date


async def _create_account(client, clerk_auth, user="user_alice", *, name="Test Account", type_="checking", starting_balance=0.0):
    r = await client.post(
        "/api/v1/accounts",
        headers=clerk_auth(user),
        json={"name": name, "type": type_, "starting_balance": starting_balance},
    )
    return r.json()["id"]


async def test_transfer_creates_two_correctly_signed_linked_legs(client, clerk_auth):
    """The core behavior: an expense leg on the source account, an
    income leg on the destination, same amount magnitude, same
    transfer_group_id, opposite signs."""
    checking = await _create_account(client, clerk_auth, starting_balance=1000)
    credit_card = await _create_account(client, clerk_auth, name="Credit Card", type_="credit_card")

    r = await client.post(
        "/api/v1/transactions/transfer",
        headers=clerk_auth("user_alice"),
        json={"from_account_id": checking, "to_account_id": credit_card, "amount": 500, "date": "2026-08-07"},
    )
    assert r.status_code == 201
    body = r.json()
    from_tx = body["from_transaction"]
    to_tx = body["to_transaction"]

    assert from_tx["account_id"] == checking
    assert from_tx["amount"] == -500.0
    assert to_tx["account_id"] == credit_card
    assert to_tx["amount"] == 500.0
    assert from_tx["transfer_group_id"] == to_tx["transfer_group_id"]
    assert from_tx["transfer_group_id"] is not None
    assert from_tx["category"] == "Transfer"
    assert to_tx["category"] == "Transfer"


async def test_transfer_moves_real_money_between_account_balances(client, clerk_auth):
    """A credit card payment IS a transfer -- checking balance drops,
    credit card balance (debt owed) drops by the same amount. This is
    the actual point of the feature: both legs are real money
    movement, not excluded from balance the way they are from stats."""
    checking = await _create_account(client, clerk_auth, starting_balance=1000)
    credit_card = await _create_account(client, clerk_auth, name="Credit Card", type_="credit_card", starting_balance=-500)

    await client.post(
        "/api/v1/transactions/transfer",
        headers=clerk_auth("user_alice"),
        json={"from_account_id": checking, "to_account_id": credit_card, "amount": 200, "date": "2026-08-07"},
    )

    r = await client.get("/api/v1/accounts", headers=clerk_auth("user_alice"))
    accounts = {a["id"]: a for a in r.json()}
    assert accounts[checking]["current_balance"] == 800.0
    assert accounts[credit_card]["current_balance"] == -300.0  # debt reduced from 500 to 300


async def test_transfer_default_notes_use_account_names(client, clerk_auth):
    checking = await _create_account(client, clerk_auth, name="Chase Checking")
    credit_card = await _create_account(client, clerk_auth, name="Chase Credit Card", type_="credit_card")

    r = await client.post(
        "/api/v1/transactions/transfer",
        headers=clerk_auth("user_alice"),
        json={"from_account_id": checking, "to_account_id": credit_card, "amount": 100, "date": "2026-08-07"},
    )
    body = r.json()
    assert body["from_transaction"]["description"] == "To Chase Credit Card"
    assert body["to_transaction"]["description"] == "From Chase Checking"


async def test_transfer_custom_note_used_for_both_legs(client, clerk_auth):
    checking = await _create_account(client, clerk_auth)
    credit_card = await _create_account(client, clerk_auth, type_="credit_card")

    r = await client.post(
        "/api/v1/transactions/transfer",
        headers=clerk_auth("user_alice"),
        json={
            "from_account_id": checking,
            "to_account_id": credit_card,
            "amount": 100,
            "date": "2026-08-07",
            "note": "August statement payment",
        },
    )
    body = r.json()
    assert body["from_transaction"]["description"] == "August statement payment"
    assert body["to_transaction"]["description"] == "August statement payment"


async def test_transfer_rejects_same_account(client, clerk_auth):
    checking = await _create_account(client, clerk_auth)
    r = await client.post(
        "/api/v1/transactions/transfer",
        headers=clerk_auth("user_alice"),
        json={"from_account_id": checking, "to_account_id": checking, "amount": 100, "date": "2026-08-07"},
    )
    assert r.status_code == 404


async def test_transfer_rejects_unowned_from_account(client, clerk_auth):
    alice_account = await _create_account(client, clerk_auth, user="user_alice")
    bob_account = await _create_account(client, clerk_auth, user="user_bob")
    r = await client.post(
        "/api/v1/transactions/transfer",
        headers=clerk_auth("user_alice"),
        json={"from_account_id": bob_account, "to_account_id": alice_account, "amount": 100, "date": "2026-08-07"},
    )
    assert r.status_code == 404


async def test_transfer_rejects_unowned_to_account(client, clerk_auth):
    alice_account = await _create_account(client, clerk_auth, user="user_alice")
    bob_account = await _create_account(client, clerk_auth, user="user_bob")
    r = await client.post(
        "/api/v1/transactions/transfer",
        headers=clerk_auth("user_alice"),
        json={"from_account_id": alice_account, "to_account_id": bob_account, "amount": 100, "date": "2026-08-07"},
    )
    assert r.status_code == 404


async def test_transfer_rejects_non_positive_amount(client, clerk_auth):
    checking = await _create_account(client, clerk_auth)
    credit_card = await _create_account(client, clerk_auth, type_="credit_card")
    r = await client.post(
        "/api/v1/transactions/transfer",
        headers=clerk_auth("user_alice"),
        json={"from_account_id": checking, "to_account_id": credit_card, "amount": 0, "date": "2026-08-07"},
    )
    assert r.status_code == 422


async def test_deleting_one_leg_deletes_both(client, clerk_auth):
    """The deliberate improvement over ledger-app: deleting a transfer
    removes the whole pair, not just the leg that was tapped, so there's
    never a half-orphaned transfer sitting in the list."""
    checking = await _create_account(client, clerk_auth)
    credit_card = await _create_account(client, clerk_auth, type_="credit_card")

    r = await client.post(
        "/api/v1/transactions/transfer",
        headers=clerk_auth("user_alice"),
        json={"from_account_id": checking, "to_account_id": credit_card, "amount": 100, "date": "2026-08-07"},
    )
    from_id = r.json()["from_transaction"]["id"]
    to_id = r.json()["to_transaction"]["id"]

    del_r = await client.delete(f"/api/v1/transactions/{from_id}", headers=clerk_auth("user_alice"))
    assert del_r.status_code == 204

    r2 = await client.get("/api/v1/transactions", headers=clerk_auth("user_alice"))
    remaining_ids = {t["id"] for t in r2.json()}
    assert from_id not in remaining_ids
    assert to_id not in remaining_ids  # the other leg is gone too, not orphaned


async def test_regular_transaction_delete_unaffected(client, clerk_auth):
    """Confirms the transfer-pair deletion logic doesn't accidentally
    touch ordinary (non-transfer) transactions."""
    checking = await _create_account(client, clerk_auth)
    r = await client.post(
        "/api/v1/transactions",
        headers=clerk_auth("user_alice"),
        json={"account_id": checking, "amount": -20, "description": "Coffee", "date": "2026-08-07"},
    )
    tx_id = r.json()["id"]
    del_r = await client.delete(f"/api/v1/transactions/{tx_id}", headers=clerk_auth("user_alice"))
    assert del_r.status_code == 204


async def test_transfer_excluded_from_budget_spending(client, clerk_auth):
    """A transfer never counts toward any category's budget spending
    -- moving your own money between accounts is neither earning nor
    spending. Uses Housing (a real category) to make sure a transfer
    genuinely doesn't leak into spending totals, even indirectly."""
    checking = await _create_account(client, clerk_auth, starting_balance=1000)
    credit_card = await _create_account(client, clerk_auth, type_="credit_card")

    await client.put(
        "/api/v1/budgets", headers=clerk_auth("user_alice"), json={"category": "Housing", "monthly_limit": 1000}
    )
    # A real Housing expense, which SHOULD count.
    await client.post(
        "/api/v1/transactions",
        headers=clerk_auth("user_alice"),
        json={"account_id": checking, "amount": -300, "category": "Housing", "date": date.today().isoformat()},
    )
    # A large transfer, which should NOT count toward anything.
    await client.post(
        "/api/v1/transactions/transfer",
        headers=clerk_auth("user_alice"),
        json={"from_account_id": checking, "to_account_id": credit_card, "amount": 5000, "date": date.today().isoformat()},
    )

    r = await client.get("/api/v1/budgets", headers=clerk_auth("user_alice"))
    housing = next(b for b in r.json() if b["category"] == "Housing")
    assert housing["spent"] == 300.0  # not 5300 -- the transfer didn't leak in
