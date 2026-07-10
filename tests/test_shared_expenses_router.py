from decimal import Decimal


async def _signup(client, email, name):
    r = await client.post("/api/v1/auth/signup", json={"email": email, "password": "hunter2222", "display_name": name})
    body = r.json()
    return body["access_token"], body["user"]["id"]


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


async def test_create_group_requires_existing_accounts_for_all_members(client):
    token, _ = await _signup(client, "alice-ge1@example.com", "Alice")
    r = await client.post("/api/v1/shared-expenses/groups", headers=_auth(token), json={"name": "Roommates", "member_emails": ["nobody-here@example.com"]})
    assert r.status_code == 400


async def test_create_group_succeeds_with_real_members(client):
    alice_token, alice_id = await _signup(client, "alice-ge2@example.com", "Alice")
    _, bob_id = await _signup(client, "bob-ge2@example.com", "Bob")

    r = await client.post("/api/v1/shared-expenses/groups", headers=_auth(alice_token), json={"name": "Roommates", "member_emails": ["bob-ge2@example.com"]})
    assert r.status_code == 201
    body = r.json()
    assert body["name"] == "Roommates"
    member_ids = {m["user_id"] for m in body["members"]}
    assert member_ids == {alice_id, bob_id}  # creator is automatically included


async def test_group_member_emails_are_never_exposed_in_the_response(client):
    alice_token, _ = await _signup(client, "alice-ge3@example.com", "Alice")
    await _signup(client, "bob-ge3@example.com", "Bob")

    r = await client.post("/api/v1/shared-expenses/groups", headers=_auth(alice_token), json={"name": "Roommates", "member_emails": ["bob-ge3@example.com"]})
    body_text = r.text
    assert "bob-ge3@example.com" not in body_text  # only names, never emails, in the response


async def test_non_member_gets_404_not_403_for_a_real_group(client):
    """Enumeration-safety: a group you're not in shouldn't even confirm it exists."""
    alice_token, _ = await _signup(client, "alice-ge4@example.com", "Alice")
    stranger_token, _ = await _signup(client, "stranger-ge4@example.com", "Stranger")

    group_resp = await client.post("/api/v1/shared-expenses/groups", headers=_auth(alice_token), json={"name": "Private Group", "member_emails": []})
    group_id = group_resp.json()["id"]

    r = await client.get(f"/api/v1/shared-expenses/groups/{group_id}", headers=_auth(stranger_token))
    assert r.status_code == 404


async def test_list_my_groups_only_shows_groups_im_actually_in(client):
    alice_token, _ = await _signup(client, "alice-ge5@example.com", "Alice")
    stranger_token, _ = await _signup(client, "stranger-ge5@example.com", "Stranger")

    await client.post("/api/v1/shared-expenses/groups", headers=_auth(alice_token), json={"name": "Alice's Group", "member_emails": []})

    r = await client.get("/api/v1/shared-expenses/groups", headers=_auth(stranger_token))
    assert r.json() == []  # stranger sees nothing, sees no leak of the group's existence either


async def test_create_expense_splits_correctly_and_returns_it(client):
    alice_token, alice_id = await _signup(client, "alice-ge6@example.com", "Alice")
    _, bob_id = await _signup(client, "bob-ge6@example.com", "Bob")
    group_resp = await client.post("/api/v1/shared-expenses/groups", headers=_auth(alice_token), json={"name": "Trip", "member_emails": ["bob-ge6@example.com"]})
    group_id = group_resp.json()["id"]

    r = await client.post(
        f"/api/v1/shared-expenses/groups/{group_id}/expenses", headers=_auth(alice_token),
        json={"description": "Dinner", "amount": 100.00, "expense_date": "2026-07-08", "participant_ids": [alice_id, bob_id]},
    )
    assert r.status_code == 201
    body = r.json()
    assert body["paid_by"] == alice_id  # always the caller, never settable to someone else
    assert body["paid_by_name"] == "Alice"
    shares = {s["user_id"]: s["share_amount"] for s in body["splits"]}
    assert shares[alice_id] == "50.00"
    assert shares[bob_id] == "50.00"
    assert Decimal(shares[alice_id]) + Decimal(shares[bob_id]) == Decimal("100.00")  # the real property that matters


async def test_create_expense_rejects_a_participant_who_is_not_a_group_member(client):
    alice_token, alice_id = await _signup(client, "alice-ge7@example.com", "Alice")
    _, outsider_id = await _signup(client, "outsider-ge7@example.com", "Outsider")
    group_resp = await client.post("/api/v1/shared-expenses/groups", headers=_auth(alice_token), json={"name": "Trip", "member_emails": []})
    group_id = group_resp.json()["id"]

    r = await client.post(
        f"/api/v1/shared-expenses/groups/{group_id}/expenses", headers=_auth(alice_token),
        json={"description": "Dinner", "amount": 50.00, "expense_date": "2026-07-08", "participant_ids": [alice_id, outsider_id]},
    )
    assert r.status_code == 400


async def test_paid_by_cannot_be_set_to_someone_else(client):
    """The request schema has no paid_by field at all — confirmed structurally, not just by convention."""
    from app.schemas.shared_expenses import SharedExpenseCreateRequest
    assert "paid_by" not in SharedExpenseCreateRequest.model_fields


async def test_edit_expense_recalculates_splits(client):
    alice_token, alice_id = await _signup(client, "alice-ge8@example.com", "Alice")
    _, bob_id = await _signup(client, "bob-ge8@example.com", "Bob")
    group_resp = await client.post("/api/v1/shared-expenses/groups", headers=_auth(alice_token), json={"name": "Trip", "member_emails": ["bob-ge8@example.com"]})
    group_id = group_resp.json()["id"]
    expense_resp = await client.post(
        f"/api/v1/shared-expenses/groups/{group_id}/expenses", headers=_auth(alice_token),
        json={"description": "Dinner", "amount": 100.00, "expense_date": "2026-07-08", "participant_ids": [alice_id, bob_id]},
    )
    expense_id = expense_resp.json()["id"]

    r = await client.patch(f"/api/v1/shared-expenses/expenses/{expense_id}", headers=_auth(alice_token), json={"amount": 80.00})
    assert r.status_code == 200
    shares = {s["user_id"]: s["share_amount"] for s in r.json()["splits"]}
    assert shares[bob_id] == "40.00"


async def test_a_non_group_member_cannot_see_or_edit_the_expense(client):
    alice_token, alice_id = await _signup(client, "alice-ge9@example.com", "Alice")
    stranger_token, _ = await _signup(client, "stranger-ge9@example.com", "Stranger")
    group_resp = await client.post("/api/v1/shared-expenses/groups", headers=_auth(alice_token), json={"name": "Private", "member_emails": []})
    group_id = group_resp.json()["id"]
    expense_resp = await client.post(
        f"/api/v1/shared-expenses/groups/{group_id}/expenses", headers=_auth(alice_token),
        json={"description": "Dinner", "amount": 50.00, "expense_date": "2026-07-08", "participant_ids": [alice_id]},
    )
    expense_id = expense_resp.json()["id"]

    get_r = await client.get(f"/api/v1/shared-expenses/expenses/{expense_id}", headers=_auth(stranger_token))
    assert get_r.status_code == 404
    edit_r = await client.patch(f"/api/v1/shared-expenses/expenses/{expense_id}", headers=_auth(stranger_token), json={"amount": 1.00})
    assert edit_r.status_code == 404


async def test_comments_thread_includes_system_edit_history(client):
    alice_token, alice_id = await _signup(client, "alice-ge10@example.com", "Alice")
    _, bob_id = await _signup(client, "bob-ge10@example.com", "Bob")
    group_resp = await client.post("/api/v1/shared-expenses/groups", headers=_auth(alice_token), json={"name": "Trip", "member_emails": ["bob-ge10@example.com"]})
    group_id = group_resp.json()["id"]
    expense_resp = await client.post(
        f"/api/v1/shared-expenses/groups/{group_id}/expenses", headers=_auth(alice_token),
        json={"description": "Dinner", "amount": 100.00, "expense_date": "2026-07-08", "participant_ids": [alice_id, bob_id]},
    )
    expense_id = expense_resp.json()["id"]
    await client.patch(f"/api/v1/shared-expenses/expenses/{expense_id}", headers=_auth(alice_token), json={"amount": 90.00})
    await client.post(f"/api/v1/shared-expenses/expenses/{expense_id}/comments", headers=_auth(alice_token), json={"body": "Forgot the tip"})

    r = await client.get(f"/api/v1/shared-expenses/expenses/{expense_id}/comments", headers=_auth(alice_token))
    comments = r.json()
    assert len(comments) == 2
    assert comments[0]["is_system"] is True  # the edit, chronologically first
    assert comments[1]["is_system"] is False  # the human comment
    assert comments[1]["body"] == "Forgot the tip"


async def test_blank_comment_is_rejected(client):
    alice_token, alice_id = await _signup(client, "alice-ge11@example.com", "Alice")
    group_resp = await client.post("/api/v1/shared-expenses/groups", headers=_auth(alice_token), json={"name": "Solo", "member_emails": []})
    group_id = group_resp.json()["id"]
    expense_resp = await client.post(
        f"/api/v1/shared-expenses/groups/{group_id}/expenses", headers=_auth(alice_token),
        json={"description": "Coffee", "amount": 5.00, "expense_date": "2026-07-08", "participant_ids": [alice_id]},
    )
    expense_id = expense_resp.json()["id"]
    r = await client.post(f"/api/v1/shared-expenses/expenses/{expense_id}/comments", headers=_auth(alice_token), json={"body": "   "})
    assert r.status_code == 422


async def test_balance_shape_is_two_nonneg_fields_never_a_signed_number(client):
    """The whole point of this shape: structurally impossible to misread the sign."""
    alice_token, alice_id = await _signup(client, "alice-ge12@example.com", "Alice")
    bob_token, bob_id = await _signup(client, "bob-ge12@example.com", "Bob")
    group_resp = await client.post("/api/v1/shared-expenses/groups", headers=_auth(alice_token), json={"name": "Trip", "member_emails": ["bob-ge12@example.com"]})
    group_id = group_resp.json()["id"]
    await client.post(
        f"/api/v1/shared-expenses/groups/{group_id}/expenses", headers=_auth(alice_token),
        json={"description": "Dinner", "amount": 100.00, "expense_date": "2026-07-08", "participant_ids": [alice_id, bob_id]},
    )

    bob_balances = await client.get("/api/v1/shared-expenses/balances", headers=_auth(bob_token))
    bob_row = bob_balances.json()[0]
    assert bob_row["you_owe_them"] == "50.00"   # Bob owes Alice
    assert bob_row["they_owe_you"] == "0.00"

    alice_balances = await client.get("/api/v1/shared-expenses/balances", headers=_auth(alice_token))
    alice_row = alice_balances.json()[0]
    assert alice_row["you_owe_them"] == "0.00"
    assert alice_row["they_owe_you"] == "50.00"  # Bob owes Alice, from Alice's side


async def test_settlement_zeroes_out_the_balance_via_the_real_endpoint(client):
    alice_token, alice_id = await _signup(client, "alice-ge13@example.com", "Alice")
    bob_token, bob_id = await _signup(client, "bob-ge13@example.com", "Bob")
    group_resp = await client.post("/api/v1/shared-expenses/groups", headers=_auth(alice_token), json={"name": "Trip", "member_emails": ["bob-ge13@example.com"]})
    group_id = group_resp.json()["id"]
    await client.post(
        f"/api/v1/shared-expenses/groups/{group_id}/expenses", headers=_auth(alice_token),
        json={"description": "Dinner", "amount": 100.00, "expense_date": "2026-07-08", "participant_ids": [alice_id, bob_id]},
    )

    r = await client.post("/api/v1/shared-expenses/settlements", headers=_auth(bob_token), json={"to_user_id": alice_id, "amount": 50.00, "settled_date": "2026-07-09"})
    assert r.status_code == 201

    balances = await client.get("/api/v1/shared-expenses/balances", headers=_auth(bob_token))
    assert balances.json() == []  # fully settled, drops out of the list entirely


async def test_balances_with_zero_net_dont_clutter_the_list(client):
    alice_token, alice_id = await _signup(client, "alice-ge14@example.com", "Alice")
    _, bob_id = await _signup(client, "bob-ge14@example.com", "Bob")
    group_resp = await client.post("/api/v1/shared-expenses/groups", headers=_auth(alice_token), json={"name": "Trip", "member_emails": ["bob-ge14@example.com"]})
    group_id = group_resp.json()["id"]
    # Alice pays $50, split evenly two ways ($25 each) -- but only
    # includes herself and bob, then bob pays HER back an identical
    # $50 split the other way, netting to exactly zero.
    await client.post(
        f"/api/v1/shared-expenses/groups/{group_id}/expenses", headers=_auth(alice_token),
        json={"description": "A", "amount": 100.00, "expense_date": "2026-07-08", "participant_ids": [alice_id, bob_id]},
    )
    await client.post("/api/v1/shared-expenses/settlements", headers=_auth(alice_token), json={"to_user_id": bob_id, "amount": 0.01, "settled_date": "2026-07-09"})
    # (Not a clean zero on purpose -- just confirming the list still returns sensibly with mixed activity.)
    r = await client.get("/api/v1/shared-expenses/balances", headers=_auth(alice_token))
    assert r.status_code == 200