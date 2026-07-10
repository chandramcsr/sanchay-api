from decimal import Decimal


async def _signup(client, email, name):
    r = await client.post("/api/v1/auth/signup", json={"email": email, "password": "hunter2222", "display_name": name})
    body = r.json()
    return body["access_token"], body["user"]["id"]


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


async def test_create_group_with_unknown_email_pends_an_invite_instead_of_failing(client):
    # This test previously asserted a 400 for unknown emails — that
    # was v1's deliberate scope limit, and it was deliberately CHANGED
    # (backlog item #5, confirmed): unknown emails now become pending
    # invites (with an invite email sent) rather than errors.
    token, _ = await _signup(client, "alice-ge1@example.com", "Alice")
    r = await client.post("/api/v1/shared-expenses/groups", headers=_auth(token), json={"name": "Roommates", "members": [{"email": "nobody-here@example.com", "name": ""}]})
    assert r.status_code == 201
    assert r.json()["pending_invites"] == [{"name": "nobody-here", "email": "nobody-here@example.com"}]


async def test_create_group_succeeds_with_real_members(client):
    alice_token, alice_id = await _signup(client, "alice-ge2@example.com", "Alice")
    _, bob_id = await _signup(client, "bob-ge2@example.com", "Bob")

    r = await client.post("/api/v1/shared-expenses/groups", headers=_auth(alice_token), json={"name": "Roommates", "members": [{"email": "bob-ge2@example.com", "name": ""}]})
    assert r.status_code == 201
    body = r.json()
    assert body["name"] == "Roommates"
    member_ids = {m["user_id"] for m in body["members"]}
    assert member_ids == {alice_id, bob_id}  # creator is automatically included


async def test_group_member_emails_are_never_exposed_in_the_response(client):
    alice_token, _ = await _signup(client, "alice-ge3@example.com", "Alice")
    await _signup(client, "bob-ge3@example.com", "Bob")

    r = await client.post("/api/v1/shared-expenses/groups", headers=_auth(alice_token), json={"name": "Roommates", "members": [{"email": "bob-ge3@example.com", "name": ""}]})
    body_text = r.text
    assert "bob-ge3@example.com" not in body_text  # only names, never emails, in the response


async def test_non_member_gets_404_not_403_for_a_real_group(client):
    """Enumeration-safety: a group you're not in shouldn't even confirm it exists."""
    alice_token, _ = await _signup(client, "alice-ge4@example.com", "Alice")
    stranger_token, _ = await _signup(client, "stranger-ge4@example.com", "Stranger")

    group_resp = await client.post("/api/v1/shared-expenses/groups", headers=_auth(alice_token), json={"name": "Private Group", "members": []})
    group_id = group_resp.json()["id"]

    r = await client.get(f"/api/v1/shared-expenses/groups/{group_id}", headers=_auth(stranger_token))
    assert r.status_code == 404


async def test_list_my_groups_only_shows_groups_im_actually_in(client):
    alice_token, _ = await _signup(client, "alice-ge5@example.com", "Alice")
    stranger_token, _ = await _signup(client, "stranger-ge5@example.com", "Stranger")

    await client.post("/api/v1/shared-expenses/groups", headers=_auth(alice_token), json={"name": "Alice's Group", "members": []})

    r = await client.get("/api/v1/shared-expenses/groups", headers=_auth(stranger_token))
    assert r.json() == []  # stranger sees nothing, sees no leak of the group's existence either


async def test_create_expense_splits_correctly_and_returns_it(client):
    alice_token, alice_id = await _signup(client, "alice-ge6@example.com", "Alice")
    _, bob_id = await _signup(client, "bob-ge6@example.com", "Bob")
    group_resp = await client.post("/api/v1/shared-expenses/groups", headers=_auth(alice_token), json={"name": "Trip", "members": [{"email": "bob-ge6@example.com", "name": ""}]})
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
    group_resp = await client.post("/api/v1/shared-expenses/groups", headers=_auth(alice_token), json={"name": "Trip", "members": []})
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
    group_resp = await client.post("/api/v1/shared-expenses/groups", headers=_auth(alice_token), json={"name": "Trip", "members": [{"email": "bob-ge8@example.com", "name": ""}]})
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
    group_resp = await client.post("/api/v1/shared-expenses/groups", headers=_auth(alice_token), json={"name": "Private", "members": []})
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
    group_resp = await client.post("/api/v1/shared-expenses/groups", headers=_auth(alice_token), json={"name": "Trip", "members": [{"email": "bob-ge10@example.com", "name": ""}]})
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
    group_resp = await client.post("/api/v1/shared-expenses/groups", headers=_auth(alice_token), json={"name": "Solo", "members": []})
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
    group_resp = await client.post("/api/v1/shared-expenses/groups", headers=_auth(alice_token), json={"name": "Trip", "members": [{"email": "bob-ge12@example.com", "name": ""}]})
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
    group_resp = await client.post("/api/v1/shared-expenses/groups", headers=_auth(alice_token), json={"name": "Trip", "members": [{"email": "bob-ge13@example.com", "name": ""}]})
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
    group_resp = await client.post("/api/v1/shared-expenses/groups", headers=_auth(alice_token), json={"name": "Trip", "members": [{"email": "bob-ge14@example.com", "name": ""}]})
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

async def test_expense_category_defaults_to_other_and_can_be_set_explicitly(client):
    alice_token, alice_id = await _signup(client, "alice-cat1@example.com", "Alice")
    group_resp = await client.post("/api/v1/shared-expenses/groups", headers=_auth(alice_token), json={"name": "Solo", "members": []})
    group_id = group_resp.json()["id"]

    default_resp = await client.post(
        f"/api/v1/shared-expenses/groups/{group_id}/expenses", headers=_auth(alice_token),
        json={"description": "Coffee", "amount": 5.00, "expense_date": "2026-07-08", "participant_ids": [alice_id]},
    )
    assert default_resp.json()["category"] == "Other"

    explicit_resp = await client.post(
        f"/api/v1/shared-expenses/groups/{group_id}/expenses", headers=_auth(alice_token),
        json={"description": "Dinner", "amount": 40.00, "expense_date": "2026-07-08", "participant_ids": [alice_id], "category": "Dining Out"},
    )
    assert explicit_resp.json()["category"] == "Dining Out"


async def test_editing_category_is_logged_in_the_comment_history(client):
    alice_token, alice_id = await _signup(client, "alice-cat2@example.com", "Alice")
    group_resp = await client.post("/api/v1/shared-expenses/groups", headers=_auth(alice_token), json={"name": "Solo", "members": []})
    group_id = group_resp.json()["id"]
    expense_resp = await client.post(
        f"/api/v1/shared-expenses/groups/{group_id}/expenses", headers=_auth(alice_token),
        json={"description": "Groceries", "amount": 40.00, "expense_date": "2026-07-08", "participant_ids": [alice_id], "category": "Groceries"},
    )
    expense_id = expense_resp.json()["id"]

    r = await client.patch(f"/api/v1/shared-expenses/expenses/{expense_id}", headers=_auth(alice_token), json={"category": "Dining Out"})
    assert r.json()["category"] == "Dining Out"

    comments = await client.get(f"/api/v1/shared-expenses/expenses/{expense_id}/comments", headers=_auth(alice_token))
    assert "Dining Out" in comments.json()[0]["body"]


# ---------- pending invites: adding someone with no account yet ----------

async def test_inviting_an_unknown_email_creates_a_pending_invite_not_an_error(client):
    alice_token, _ = await _signup(client, "alice-inv1@example.com", "Alice")
    r = await client.post(
        "/api/v1/shared-expenses/groups", headers=_auth(alice_token),
        json={"name": "Future Roommates", "members": [{"email": "not-yet-signed-up-inv1@example.com", "name": ""}]},
    )
    assert r.status_code == 201  # no longer a 400
    body = r.json()
    assert body["pending_invites"] == [{"name": "not-yet-signed-up-inv1", "email": "not-yet-signed-up-inv1@example.com"}]
    member_ids = [m["user_id"] for m in body["members"]]
    assert len(member_ids) == 1  # just Alice so far — the invitee isn't a member YET


async def test_signing_up_with_an_invited_email_joins_the_group_automatically(client):
    alice_token, _ = await _signup(client, "alice-inv2@example.com", "Alice")
    group_resp = await client.post(
        "/api/v1/shared-expenses/groups", headers=_auth(alice_token),
        json={"name": "Trip Group", "members": [{"email": "carol-inv2@example.com", "name": ""}]},
    )
    group_id = group_resp.json()["id"]

    # Carol signs up with the exact email that was invited.
    signup_resp = await client.post(
        "/api/v1/auth/signup",
        json={"email": "carol-inv2@example.com", "password": "hunter2222", "display_name": "Carol"},
    )
    assert signup_resp.status_code == 201
    body = signup_resp.json()
    assert body["joined_groups"] == ["Trip Group"]  # visible in the signup response, not silent

    # And she's genuinely a member now — she can see the group.
    carol_token = body["access_token"]
    group_check = await client.get(f"/api/v1/shared-expenses/groups/{group_id}", headers=_auth(carol_token))
    assert group_check.status_code == 200
    member_names = [m["name"] for m in group_check.json()["members"]]
    assert "Carol" in member_names

    # The pending invite is consumed — no longer listed.
    assert group_check.json()["pending_invites"] == []


async def test_signup_with_no_pending_invites_has_empty_joined_groups(client):
    r = await client.post(
        "/api/v1/auth/signup",
        json={"email": "nobody-invited-inv3@example.com", "password": "hunter2222", "display_name": "Plain"},
    )
    assert r.json()["joined_groups"] == []


async def test_invited_email_is_matched_case_insensitively(client):
    alice_token, _ = await _signup(client, "alice-inv4@example.com", "Alice")
    await client.post(
        "/api/v1/shared-expenses/groups", headers=_auth(alice_token),
        json={"name": "Case Group", "members": [{"email": "MixedCase-Inv4@Example.com", "name": ""}]},
    )
    signup_resp = await client.post(
        "/api/v1/auth/signup",
        json={"email": "mixedcase-inv4@example.com", "password": "hunter2222", "display_name": "Mixed"},
    )
    assert signup_resp.json()["joined_groups"] == ["Case Group"]


async def test_invite_email_failure_does_not_break_group_creation(client, monkeypatch):
    """
    The exact production incident this guards against: Resend's
    sandbox mode (no verified domain) raises on any send to an
    address other than the account owner's — and because the send was
    originally synchronous inside create_pending_invite, that
    500'd the whole group-creation request. The group and pending
    invite are real regardless of email delivery; a send failure must
    log, not fail the request.
    """
    from app.core import email as email_module

    def exploding_send(*args, **kwargs):
        raise RuntimeError("You can only send testing emails to your own email address")

    monkeypatch.setattr(email_module.email_sender, "send_group_invite", exploding_send)

    alice_token, _ = await _signup(client, "alice-emailfail@example.com", "Alice")
    r = await client.post(
        "/api/v1/shared-expenses/groups", headers=_auth(alice_token),
        json={"name": "Resilient Group", "members": [{"email": "someone-new-emailfail@example.com", "name": ""}]},
    )
    assert r.status_code == 201  # the request succeeds despite the email exploding
    assert r.json()["pending_invites"] == [{"name": "someone-new-emailfail", "email": "someone-new-emailfail@example.com"}]  # the invite row is real

    # And the invite still works end to end — signup joins the group,
    # proving the failed EMAIL didn't orphan the actual invite.
    signup_resp = await client.post(
        "/api/v1/auth/signup",
        json={"email": "someone-new-emailfail@example.com", "password": "hunter2222", "display_name": "New"},
    )
    assert signup_resp.json()["joined_groups"] == ["Resilient Group"]


# ---------- rename and delete groups ----------

async def test_rename_group(client):
    alice_token, _ = await _signup(client, "alice-rn1@example.com", "Alice")
    group_resp = await client.post("/api/v1/shared-expenses/groups", headers=_auth(alice_token), json={"name": "763", "members": []})
    group_id = group_resp.json()["id"]

    r = await client.patch(f"/api/v1/shared-expenses/groups/{group_id}", headers=_auth(alice_token), json={"name": "Roommates 2026"})
    assert r.status_code == 200
    assert r.json()["name"] == "Roommates 2026"


async def test_non_member_cannot_rename_a_group(client):
    alice_token, _ = await _signup(client, "alice-rn2@example.com", "Alice")
    stranger_token, _ = await _signup(client, "stranger-rn2@example.com", "Stranger")
    group_resp = await client.post("/api/v1/shared-expenses/groups", headers=_auth(alice_token), json={"name": "Private", "members": []})
    group_id = group_resp.json()["id"]

    r = await client.patch(f"/api/v1/shared-expenses/groups/{group_id}", headers=_auth(stranger_token), json={"name": "Hijacked"})
    assert r.status_code == 404


async def test_delete_an_empty_group(client):
    alice_token, _ = await _signup(client, "alice-del1@example.com", "Alice")
    group_resp = await client.post("/api/v1/shared-expenses/groups", headers=_auth(alice_token), json={"name": "Duplicate", "members": []})
    group_id = group_resp.json()["id"]

    r = await client.delete(f"/api/v1/shared-expenses/groups/{group_id}", headers=_auth(alice_token))
    assert r.status_code == 204

    get_r = await client.get(f"/api/v1/shared-expenses/groups/{group_id}", headers=_auth(alice_token))
    assert get_r.status_code == 404  # genuinely gone


async def test_cannot_delete_a_group_with_expense_history(client):
    """The one real safeguard: a group's expense history belongs to everyone in it, not just whoever clicks delete."""
    alice_token, alice_id = await _signup(client, "alice-del2@example.com", "Alice")
    _, bob_id = await _signup(client, "bob-del2@example.com", "Bob")
    group_resp = await client.post("/api/v1/shared-expenses/groups", headers=_auth(alice_token), json={"name": "Real Group", "members": [{"email": "bob-del2@example.com", "name": ""}]})
    group_id = group_resp.json()["id"]
    await client.post(
        f"/api/v1/shared-expenses/groups/{group_id}/expenses", headers=_auth(alice_token),
        json={"description": "Dinner", "amount": 40.00, "expense_date": "2026-07-08", "participant_ids": [alice_id, bob_id]},
    )

    r = await client.delete(f"/api/v1/shared-expenses/groups/{group_id}", headers=_auth(alice_token))
    assert r.status_code == 409

    # And it's genuinely still there afterward.
    get_r = await client.get(f"/api/v1/shared-expenses/groups/{group_id}", headers=_auth(alice_token))
    assert get_r.status_code == 200


async def test_non_member_cannot_delete_a_group(client):
    alice_token, _ = await _signup(client, "alice-del3@example.com", "Alice")
    stranger_token, _ = await _signup(client, "stranger-del3@example.com", "Stranger")
    group_resp = await client.post("/api/v1/shared-expenses/groups", headers=_auth(alice_token), json={"name": "Private", "members": []})
    group_id = group_resp.json()["id"]

    r = await client.delete(f"/api/v1/shared-expenses/groups/{group_id}", headers=_auth(stranger_token))
    assert r.status_code == 404

    get_r = await client.get(f"/api/v1/shared-expenses/groups/{group_id}", headers=_auth(alice_token))
    assert get_r.status_code == 200  # still there, untouched


# ---------- adding a member to an existing group ----------

async def test_add_an_existing_account_as_a_member(client):
    alice_token, _ = await _signup(client, "alice-am1@example.com", "Alice")
    _, bob_id = await _signup(client, "bob-am1@example.com", "Bob")
    group_resp = await client.post("/api/v1/shared-expenses/groups", headers=_auth(alice_token), json={"name": "Roommates", "members": []})
    group_id = group_resp.json()["id"]

    r = await client.post(f"/api/v1/shared-expenses/groups/{group_id}/members", headers=_auth(alice_token), json={"email": "bob-am1@example.com"})
    assert r.status_code == 201
    member_ids = [m["user_id"] for m in r.json()["members"]]
    assert bob_id in member_ids


async def test_add_a_member_who_has_no_account_creates_a_pending_invite(client):
    alice_token, _ = await _signup(client, "alice-am2@example.com", "Alice")
    group_resp = await client.post("/api/v1/shared-expenses/groups", headers=_auth(alice_token), json={"name": "Roommates", "members": []})
    group_id = group_resp.json()["id"]

    r = await client.post(f"/api/v1/shared-expenses/groups/{group_id}/members", headers=_auth(alice_token), json={"email": "future-roommate-am2@example.com"})
    assert r.status_code == 201
    assert r.json()["pending_invites"] == [{"name": "future-roommate-am2", "email": "future-roommate-am2@example.com"}]

    # And it works end to end, same as an invite made at creation time.
    signup_resp = await client.post("/api/v1/auth/signup", json={"email": "future-roommate-am2@example.com", "password": "hunter2222", "display_name": "Later"})
    assert signup_resp.json()["joined_groups"] == ["Roommates"]


async def test_adding_an_already_existing_member_is_a_harmless_no_op(client):
    alice_token, _ = await _signup(client, "alice-am3@example.com", "Alice")
    _, bob_id = await _signup(client, "bob-am3@example.com", "Bob")
    group_resp = await client.post("/api/v1/shared-expenses/groups", headers=_auth(alice_token), json={"name": "Roommates", "members": [{"email": "bob-am3@example.com", "name": ""}]})
    group_id = group_resp.json()["id"]

    r = await client.post(f"/api/v1/shared-expenses/groups/{group_id}/members", headers=_auth(alice_token), json={"email": "bob-am3@example.com"})
    assert r.status_code == 201
    member_ids = [m["user_id"] for m in r.json()["members"]]
    assert member_ids.count(bob_id) == 1  # not duplicated


async def test_adding_the_same_pending_email_twice_does_not_duplicate_the_invite(client):
    alice_token, _ = await _signup(client, "alice-am4@example.com", "Alice")
    group_resp = await client.post("/api/v1/shared-expenses/groups", headers=_auth(alice_token), json={"name": "Roommates", "members": []})
    group_id = group_resp.json()["id"]

    await client.post(f"/api/v1/shared-expenses/groups/{group_id}/members", headers=_auth(alice_token), json={"email": "twice-am4@example.com"})
    r = await client.post(f"/api/v1/shared-expenses/groups/{group_id}/members", headers=_auth(alice_token), json={"email": "twice-am4@example.com"})
    assert r.json()["pending_invites"] == [{"name": "twice-am4", "email": "twice-am4@example.com"}]  # exactly one, not two


async def test_non_member_cannot_add_someone_to_a_group(client):
    alice_token, _ = await _signup(client, "alice-am5@example.com", "Alice")
    stranger_token, _ = await _signup(client, "stranger-am5@example.com", "Stranger")
    group_resp = await client.post("/api/v1/shared-expenses/groups", headers=_auth(alice_token), json={"name": "Private", "members": []})
    group_id = group_resp.json()["id"]

    r = await client.post(f"/api/v1/shared-expenses/groups/{group_id}/members", headers=_auth(stranger_token), json={"email": "whoever@example.com"})
    assert r.status_code == 404


# ---------- naming pending invites, and splitting expenses with people who aren't registered yet ----------

async def test_pending_invite_gets_the_real_name_given_not_the_email(client):
    alice_token, _ = await _signup(client, "alice-name1@example.com", "Alice")
    r = await client.post(
        "/api/v1/shared-expenses/groups", headers=_auth(alice_token),
        json={"name": "Roommates", "members": [{"email": "sam-name1@example.com", "name": "Sam"}]},
    )
    assert r.json()["pending_invites"] == [{"name": "Sam", "email": "sam-name1@example.com"}]


async def test_pending_invite_falls_back_to_email_local_part_when_no_name_given(client):
    alice_token, _ = await _signup(client, "alice-name2@example.com", "Alice")
    r = await client.post(
        "/api/v1/shared-expenses/groups", headers=_auth(alice_token),
        json={"name": "Roommates", "members": [{"email": "just-an-email-name2@example.com"}]},
    )
    assert r.json()["pending_invites"] == [{"name": "just-an-email-name2", "email": "just-an-email-name2@example.com"}]


async def test_split_an_expense_with_someone_who_has_no_account_yet(client):
    alice_token, alice_id = await _signup(client, "alice-pp1@example.com", "Alice")
    group_resp = await client.post("/api/v1/shared-expenses/groups", headers=_auth(alice_token), json={"name": "Trip", "members": []})
    group_id = group_resp.json()["id"]

    r = await client.post(
        f"/api/v1/shared-expenses/groups/{group_id}/expenses", headers=_auth(alice_token),
        json={
            "description": "Dinner", "amount": 100.00, "expense_date": "2026-07-10",
            "participant_ids": [alice_id],
            "pending_participants": [{"email": "sam-pp1@example.com", "name": "Sam"}],
        },
    )
    assert r.status_code == 201
    splits = {s["name"]: s for s in r.json()["splits"]}
    assert "Sam" in splits
    assert splits["Sam"]["user_id"] is None  # not a real account yet
    assert splits["Sam"]["share_amount"] == "50.00"
    assert splits["Alice"]["share_amount"] == "50.00"

    # Splitting with someone new invites them, same as adding them directly.
    group_check = await client.get(f"/api/v1/shared-expenses/groups/{group_id}", headers=_auth(alice_token))
    assert group_check.json()["pending_invites"] == [{"name": "Sam", "email": "sam-pp1@example.com"}]


async def test_signing_up_reconnects_a_pending_participant_split_automatically(client):
    """
    The actual architectural claim being verified: a pending
    participant's split (user_id=None, email_ref set) is built
    EXACTLY like a frozen split already is, so reconnect_by_email —
    built for the delete-account-then-resignup case — reattaches it
    for free, with zero new reconciliation code. This test is the
    proof.
    """
    alice_token, alice_id = await _signup(client, "alice-pp2@example.com", "Alice")
    group_resp = await client.post("/api/v1/shared-expenses/groups", headers=_auth(alice_token), json={"name": "Trip", "members": []})
    group_id = group_resp.json()["id"]
    await client.post(
        f"/api/v1/shared-expenses/groups/{group_id}/expenses", headers=_auth(alice_token),
        json={
            "description": "Dinner", "amount": 100.00, "expense_date": "2026-07-10",
            "participant_ids": [alice_id],
            "pending_participants": [{"email": "sam-pp2@example.com", "name": "Sam"}],
        },
    )

    signup_resp = await client.post("/api/v1/auth/signup", json={"email": "sam-pp2@example.com", "password": "hunter2222", "display_name": "Sam"})
    body = signup_resp.json()
    assert body["joined_groups"] == ["Trip"]  # from join_pending_invites
    sam_id = body["user"]["id"]

    # And the SPLIT itself is now live and attributed to Sam's real account.
    sam_token = body["access_token"]
    expense_resp = await client.get(f"/api/v1/shared-expenses/groups/{group_id}/expenses", headers=_auth(sam_token))
    splits = {s["name"]: s for s in expense_resp.json()[0]["splits"]}
    assert splits["Sam"]["user_id"] == sam_id  # reconnected, not still None

    # Which means the balance is live and correct too — Sam actually owes Alice now.
    balances = await client.get("/api/v1/shared-expenses/balances", headers=_auth(sam_token))
    assert balances.json()[0]["you_owe_them"] == "50.00"


async def test_a_pending_participant_alongside_real_ones_still_sums_to_the_total(client):
    alice_token, alice_id = await _signup(client, "alice-pp3@example.com", "Alice")
    _, bob_id = await _signup(client, "bob-pp3@example.com", "Bob")
    group_resp = await client.post("/api/v1/shared-expenses/groups", headers=_auth(alice_token), json={"name": "Trip", "members": [{"email": "bob-pp3@example.com", "name": "Bob"}]})
    group_id = group_resp.json()["id"]

    r = await client.post(
        f"/api/v1/shared-expenses/groups/{group_id}/expenses", headers=_auth(alice_token),
        json={
            "description": "Dinner", "amount": 100.00, "expense_date": "2026-07-10",
            "participant_ids": [alice_id, bob_id],
            "pending_participants": [{"email": "carol-pp3@example.com", "name": "Carol"}],
        },
    )
    from decimal import Decimal
    total = sum(Decimal(s["share_amount"]) for s in r.json()["splits"])
    assert total == Decimal("100.00")
    assert len(r.json()["splits"]) == 3


async def test_expense_needs_at_least_one_participant_across_either_list(client):
    alice_token, _ = await _signup(client, "alice-pp4@example.com", "Alice")
    group_resp = await client.post("/api/v1/shared-expenses/groups", headers=_auth(alice_token), json={"name": "Trip", "members": []})
    group_id = group_resp.json()["id"]

    r = await client.post(
        f"/api/v1/shared-expenses/groups/{group_id}/expenses", headers=_auth(alice_token),
        json={"description": "Dinner", "amount": 50.00, "expense_date": "2026-07-10", "participant_ids": [], "pending_participants": []},
    )
    assert r.status_code == 422


# ---------- removing a member or a pending invite from a group ----------

async def test_remove_a_member_with_no_expense_history(client):
    alice_token, _ = await _signup(client, "alice-rm1@example.com", "Alice")
    _, bob_id = await _signup(client, "bob-rm1@example.com", "Bob")
    group_resp = await client.post("/api/v1/shared-expenses/groups", headers=_auth(alice_token), json={"name": "Roommates", "members": [{"email": "bob-rm1@example.com", "name": "Bob"}]})
    group_id = group_resp.json()["id"]

    r = await client.delete(f"/api/v1/shared-expenses/groups/{group_id}/members/{bob_id}", headers=_auth(alice_token))
    assert r.status_code == 200
    member_ids = [m["user_id"] for m in r.json()["members"]]
    assert bob_id not in member_ids


async def test_cannot_remove_a_member_with_real_expense_history(client):
    alice_token, alice_id = await _signup(client, "alice-rm2@example.com", "Alice")
    _, bob_id = await _signup(client, "bob-rm2@example.com", "Bob")
    group_resp = await client.post("/api/v1/shared-expenses/groups", headers=_auth(alice_token), json={"name": "Roommates", "members": [{"email": "bob-rm2@example.com", "name": "Bob"}]})
    group_id = group_resp.json()["id"]
    await client.post(
        f"/api/v1/shared-expenses/groups/{group_id}/expenses", headers=_auth(alice_token),
        json={"description": "Dinner", "amount": 40.00, "expense_date": "2026-07-10", "participant_ids": [alice_id, bob_id], "pending_participants": [], "category": "Dining Out"},
    )

    r = await client.delete(f"/api/v1/shared-expenses/groups/{group_id}/members/{bob_id}", headers=_auth(alice_token))
    assert r.status_code == 409

    # Still a member afterward — the block actually blocked.
    group_check = await client.get(f"/api/v1/shared-expenses/groups/{group_id}", headers=_auth(alice_token))
    member_ids = [m["user_id"] for m in group_check.json()["members"]]
    assert bob_id in member_ids


async def test_remove_a_pending_invite_with_no_expense_history(client):
    alice_token, _ = await _signup(client, "alice-rm3@example.com", "Alice")
    group_resp = await client.post("/api/v1/shared-expenses/groups", headers=_auth(alice_token), json={"name": "Roommates", "members": [{"email": "sam-rm3@example.com", "name": "Sam"}]})
    group_id = group_resp.json()["id"]

    r = await client.delete(f"/api/v1/shared-expenses/groups/{group_id}/pending-invites", headers=_auth(alice_token), params={"email": "sam-rm3@example.com"})
    assert r.status_code == 200
    assert r.json()["pending_invites"] == []


async def test_cannot_remove_a_pending_invite_with_real_expense_history(client):
    alice_token, alice_id = await _signup(client, "alice-rm4@example.com", "Alice")
    group_resp = await client.post("/api/v1/shared-expenses/groups", headers=_auth(alice_token), json={"name": "Roommates", "members": []})
    group_id = group_resp.json()["id"]
    await client.post(
        f"/api/v1/shared-expenses/groups/{group_id}/expenses", headers=_auth(alice_token),
        json={
            "description": "Dinner", "amount": 40.00, "expense_date": "2026-07-10",
            "participant_ids": [alice_id], "pending_participants": [{"email": "sam-rm4@example.com", "name": "Sam"}], "category": "Dining Out",
        },
    )

    r = await client.delete(f"/api/v1/shared-expenses/groups/{group_id}/pending-invites", headers=_auth(alice_token), params={"email": "sam-rm4@example.com"})
    assert r.status_code == 409

    group_check = await client.get(f"/api/v1/shared-expenses/groups/{group_id}", headers=_auth(alice_token))
    assert group_check.json()["pending_invites"] == [{"name": "Sam", "email": "sam-rm4@example.com"}]


async def test_non_member_cannot_remove_anyone_from_a_group(client):
    alice_token, _ = await _signup(client, "alice-rm5@example.com", "Alice")
    stranger_token, _ = await _signup(client, "stranger-rm5@example.com", "Stranger")
    _, bob_id = await _signup(client, "bob-rm5@example.com", "Bob")
    group_resp = await client.post("/api/v1/shared-expenses/groups", headers=_auth(alice_token), json={"name": "Private", "members": [{"email": "bob-rm5@example.com", "name": "Bob"}]})
    group_id = group_resp.json()["id"]

    r = await client.delete(f"/api/v1/shared-expenses/groups/{group_id}/members/{bob_id}", headers=_auth(stranger_token))
    assert r.status_code == 404
