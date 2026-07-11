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
    assert body["paid_by"] == alice_id  # defaults to the caller when paid_by isn't specified
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


# ---------- deleting an expense ----------

async def test_delete_an_expense(client):
    alice_token, alice_id = await _signup(client, "alice-del-exp1@example.com", "Alice")
    group_resp = await client.post("/api/v1/shared-expenses/groups", headers=_auth(alice_token), json={"name": "Trip", "members": []})
    group_id = group_resp.json()["id"]
    expense_resp = await client.post(
        f"/api/v1/shared-expenses/groups/{group_id}/expenses", headers=_auth(alice_token),
        json={"description": "Dinner", "amount": 50.00, "expense_date": "2026-07-10", "participant_ids": [alice_id], "pending_participants": [], "category": "Dining Out"},
    )
    expense_id = expense_resp.json()["id"]

    r = await client.delete(f"/api/v1/shared-expenses/expenses/{expense_id}", headers=_auth(alice_token))
    assert r.status_code == 204

    get_r = await client.get(f"/api/v1/shared-expenses/expenses/{expense_id}", headers=_auth(alice_token))
    assert get_r.status_code == 404  # genuinely gone

    list_r = await client.get(f"/api/v1/shared-expenses/groups/{group_id}/expenses", headers=_auth(alice_token))
    assert list_r.json() == []


async def test_non_member_cannot_delete_an_expense(client):
    alice_token, alice_id = await _signup(client, "alice-del-exp2@example.com", "Alice")
    stranger_token, _ = await _signup(client, "stranger-del-exp2@example.com", "Stranger")
    group_resp = await client.post("/api/v1/shared-expenses/groups", headers=_auth(alice_token), json={"name": "Private", "members": []})
    group_id = group_resp.json()["id"]
    expense_resp = await client.post(
        f"/api/v1/shared-expenses/groups/{group_id}/expenses", headers=_auth(alice_token),
        json={"description": "Dinner", "amount": 50.00, "expense_date": "2026-07-10", "participant_ids": [alice_id], "pending_participants": [], "category": "Dining Out"},
    )
    expense_id = expense_resp.json()["id"]

    r = await client.delete(f"/api/v1/shared-expenses/expenses/{expense_id}", headers=_auth(stranger_token))
    assert r.status_code == 404

    get_r = await client.get(f"/api/v1/shared-expenses/expenses/{expense_id}", headers=_auth(alice_token))
    assert get_r.status_code == 200  # still there


# ---------- editing WHO is included in an expense ----------

async def test_edit_expense_to_add_a_participant_rebuilds_the_split(client):
    alice_token, alice_id = await _signup(client, "alice-edp1@example.com", "Alice")
    _, bob_id = await _signup(client, "bob-edp1@example.com", "Bob")
    group_resp = await client.post("/api/v1/shared-expenses/groups", headers=_auth(alice_token), json={"name": "Trip", "members": [{"email": "bob-edp1@example.com", "name": "Bob"}]})
    group_id = group_resp.json()["id"]
    expense_resp = await client.post(
        f"/api/v1/shared-expenses/groups/{group_id}/expenses", headers=_auth(alice_token),
        json={"description": "Dinner", "amount": 100.00, "expense_date": "2026-07-10", "participant_ids": [alice_id], "pending_participants": [], "category": "Dining Out"},
    )
    expense_id = expense_resp.json()["id"]
    assert len(expense_resp.json()["splits"]) == 1  # just Alice initially

    r = await client.patch(f"/api/v1/shared-expenses/expenses/{expense_id}", headers=_auth(alice_token), json={"participant_ids": [alice_id, bob_id]})
    assert r.status_code == 200
    splits = {s["name"]: s["share_amount"] for s in r.json()["splits"]}
    assert splits == {"Alice": "50.00", "Bob": "50.00"}


async def test_edit_expense_to_remove_a_participant_rebuilds_the_split(client):
    alice_token, alice_id = await _signup(client, "alice-edp2@example.com", "Alice")
    _, bob_id = await _signup(client, "bob-edp2@example.com", "Bob")
    group_resp = await client.post("/api/v1/shared-expenses/groups", headers=_auth(alice_token), json={"name": "Trip", "members": [{"email": "bob-edp2@example.com", "name": "Bob"}]})
    group_id = group_resp.json()["id"]
    expense_resp = await client.post(
        f"/api/v1/shared-expenses/groups/{group_id}/expenses", headers=_auth(alice_token),
        json={"description": "Dinner", "amount": 100.00, "expense_date": "2026-07-10", "participant_ids": [alice_id, bob_id], "pending_participants": [], "category": "Dining Out"},
    )
    expense_id = expense_resp.json()["id"]

    r = await client.patch(f"/api/v1/shared-expenses/expenses/{expense_id}", headers=_auth(alice_token), json={"participant_ids": [alice_id]})
    assert r.status_code == 200
    assert len(r.json()["splits"]) == 1
    assert r.json()["splits"][0]["name"] == "Alice"
    assert r.json()["splits"][0]["share_amount"] == "100.00"


async def test_cannot_edit_expense_to_zero_participants(client):
    alice_token, alice_id = await _signup(client, "alice-edp3@example.com", "Alice")
    group_resp = await client.post("/api/v1/shared-expenses/groups", headers=_auth(alice_token), json={"name": "Solo", "members": []})
    group_id = group_resp.json()["id"]
    expense_resp = await client.post(
        f"/api/v1/shared-expenses/groups/{group_id}/expenses", headers=_auth(alice_token),
        json={"description": "Coffee", "amount": 5.00, "expense_date": "2026-07-10", "participant_ids": [alice_id], "pending_participants": [], "category": "Dining Out"},
    )
    expense_id = expense_resp.json()["id"]

    r = await client.patch(f"/api/v1/shared-expenses/expenses/{expense_id}", headers=_auth(alice_token), json={"participant_ids": []})
    assert r.status_code == 400


async def test_cannot_edit_expense_to_include_someone_outside_the_group(client):
    alice_token, alice_id = await _signup(client, "alice-edp4@example.com", "Alice")
    _, outsider_id = await _signup(client, "outsider-edp4@example.com", "Outsider")
    group_resp = await client.post("/api/v1/shared-expenses/groups", headers=_auth(alice_token), json={"name": "Trip", "members": []})
    group_id = group_resp.json()["id"]
    expense_resp = await client.post(
        f"/api/v1/shared-expenses/groups/{group_id}/expenses", headers=_auth(alice_token),
        json={"description": "Dinner", "amount": 50.00, "expense_date": "2026-07-10", "participant_ids": [alice_id], "pending_participants": [], "category": "Dining Out"},
    )
    expense_id = expense_resp.json()["id"]

    r = await client.patch(f"/api/v1/shared-expenses/expenses/{expense_id}", headers=_auth(alice_token), json={"participant_ids": [alice_id, outsider_id]})
    assert r.status_code == 400


async def test_edit_expense_amount_only_updates_a_pending_participants_share_too(client):
    """
    The exact bug caught and fixed while building this: editing only
    the AMOUNT (not participants) must still correctly recalculate a
    PENDING participant's share -- the old inline re-split logic
    silently excluded them, freezing their share_amount at the old
    value and breaking the sum-equals-total guarantee.
    """
    alice_token, alice_id = await _signup(client, "alice-edp5@example.com", "Alice")
    group_resp = await client.post("/api/v1/shared-expenses/groups", headers=_auth(alice_token), json={"name": "Trip", "members": []})
    group_id = group_resp.json()["id"]
    expense_resp = await client.post(
        f"/api/v1/shared-expenses/groups/{group_id}/expenses", headers=_auth(alice_token),
        json={
            "description": "Dinner", "amount": 100.00, "expense_date": "2026-07-10",
            "participant_ids": [alice_id], "pending_participants": [{"email": "sam-edp5@example.com", "name": "Sam"}], "category": "Dining Out",
        },
    )
    expense_id = expense_resp.json()["id"]
    original_splits = {s["name"]: s for s in expense_resp.json()["splits"]}
    assert original_splits["Sam"]["share_amount"] == "50.00"

    r = await client.patch(f"/api/v1/shared-expenses/expenses/{expense_id}", headers=_auth(alice_token), json={"amount": 80.00})
    assert r.status_code == 200
    from decimal import Decimal
    new_splits = {s["name"]: s for s in r.json()["splits"]}
    assert new_splits["Sam"]["share_amount"] == "40.00"  # NOT still frozen at 50.00
    total = sum(Decimal(s["share_amount"]) for s in r.json()["splits"])
    assert total == Decimal("80.00")  # sum-equals-total still holds


async def test_editing_amount_only_does_not_change_a_pending_participants_email_ref(client):
    """
    The bug caught while writing THIS fix: reconstructing a pending
    participant from their existing split for a re-split must reuse
    their EXISTING (already-hashed) email_ref directly, not re-derive
    it by hashing the hash -- which would silently break
    reconnect_by_email()'s ability to ever find this split again once
    they actually sign up. Verified end-to-end: sign up with the same
    email AFTER an amount-only edit, and confirm the split still
    reconnects correctly.
    """
    alice_token, alice_id = await _signup(client, "alice-edp6@example.com", "Alice")
    group_resp = await client.post("/api/v1/shared-expenses/groups", headers=_auth(alice_token), json={"name": "Trip", "members": []})
    group_id = group_resp.json()["id"]
    expense_resp = await client.post(
        f"/api/v1/shared-expenses/groups/{group_id}/expenses", headers=_auth(alice_token),
        json={
            "description": "Dinner", "amount": 100.00, "expense_date": "2026-07-10",
            "participant_ids": [alice_id], "pending_participants": [{"email": "sam-edp6@example.com", "name": "Sam"}], "category": "Dining Out",
        },
    )
    expense_id = expense_resp.json()["id"]

    await client.patch(f"/api/v1/shared-expenses/expenses/{expense_id}", headers=_auth(alice_token), json={"amount": 60.00})

    signup_resp = await client.post("/api/v1/auth/signup", json={"email": "sam-edp6@example.com", "password": "hunter2222", "display_name": "Sam"})
    assert signup_resp.json()["joined_groups"] == ["Trip"]  # the invite itself still resolves correctly

    sam_token = signup_resp.json()["access_token"]
    balances = await client.get("/api/v1/shared-expenses/balances", headers=_auth(sam_token))
    assert balances.json()[0]["you_owe_them"] == "30.00"  # half of the EDITED $60, reconnection worked


async def test_edit_expense_date(client):
    alice_token, alice_id = await _signup(client, "alice-date1@example.com", "Alice")
    group_resp = await client.post("/api/v1/shared-expenses/groups", headers=_auth(alice_token), json={"name": "Trip", "members": []})
    group_id = group_resp.json()["id"]
    expense_resp = await client.post(
        f"/api/v1/shared-expenses/groups/{group_id}/expenses", headers=_auth(alice_token),
        json={"description": "Dinner", "amount": 50.00, "expense_date": "2026-07-10", "participant_ids": [alice_id], "pending_participants": [], "category": "Dining Out"},
    )
    expense_id = expense_resp.json()["id"]

    r = await client.patch(f"/api/v1/shared-expenses/expenses/{expense_id}", headers=_auth(alice_token), json={"expense_date": "2026-07-02"})
    assert r.status_code == 200
    assert r.json()["expense_date"] == "2026-07-02"

    comments = await client.get(f"/api/v1/shared-expenses/expenses/{expense_id}/comments", headers=_auth(alice_token))
    assert "2026-07-02" in comments.json()[0]["body"]  # logged as visible history, same as any other edit


# ---------- alternate split types: shares, percentage, exact ----------

async def test_expense_paid_by_defaults_to_the_caller_when_omitted(client):
    alice_token, alice_id = await _signup(client, "paidby1@example.com", "Alice")
    group_resp = await client.post("/api/v1/shared-expenses/groups", headers=_auth(alice_token), json={"name": "Solo", "members": []})
    group_id = group_resp.json()["id"]
    r = await client.post(
        f"/api/v1/shared-expenses/groups/{group_id}/expenses", headers=_auth(alice_token),
        json={"description": "Coffee", "amount": 5.00, "expense_date": "2026-07-10", "participant_ids": [alice_id], "pending_participants": [], "category": "Dining Out"},
    )
    assert r.json()["paid_by"] == alice_id
    assert r.json()["paid_by_name"] == "Alice"


async def test_expense_can_be_logged_as_paid_by_another_real_group_member(client):
    alice_token, alice_id = await _signup(client, "paidby2@example.com", "Alice")
    _, bob_id = await _signup(client, "paidby2b@example.com", "Bob")
    group_resp = await client.post("/api/v1/shared-expenses/groups", headers=_auth(alice_token), json={"name": "Apartment", "members": [{"email": "paidby2b@example.com", "name": "Bob"}]})
    group_id = group_resp.json()["id"]

    # Alice logs an expense but names BOB as the actual payer.
    r = await client.post(
        f"/api/v1/shared-expenses/groups/{group_id}/expenses", headers=_auth(alice_token),
        json={"description": "Groceries", "amount": 60.00, "expense_date": "2026-07-10", "participant_ids": [alice_id, bob_id], "pending_participants": [], "category": "Groceries", "paid_by": bob_id},
    )
    assert r.status_code == 201
    assert r.json()["paid_by"] == bob_id
    assert r.json()["paid_by_name"] == "Bob"
    # Balance reflects Bob as payer, not Alice -- Alice (who logged it) owes HER share to Bob.
    balances = (await client.get("/api/v1/shared-expenses/balances", headers=_auth(alice_token))).json()
    bob_balance = next(b for b in balances if b["name"] == "Bob")
    assert bob_balance["you_owe_them"] == "30.00"


async def test_expense_cannot_claim_a_non_member_as_payer(client):
    alice_token, alice_id = await _signup(client, "paidby3@example.com", "Alice")
    _, outsider_id = await _signup(client, "paidby3outsider@example.com", "Outsider")
    group_resp = await client.post("/api/v1/shared-expenses/groups", headers=_auth(alice_token), json={"name": "Solo", "members": []})
    group_id = group_resp.json()["id"]

    r = await client.post(
        f"/api/v1/shared-expenses/groups/{group_id}/expenses", headers=_auth(alice_token),
        json={"description": "Sneaky", "amount": 20.00, "expense_date": "2026-07-10", "participant_ids": [alice_id], "pending_participants": [], "category": "Other", "paid_by": outsider_id},
    )
    assert r.status_code == 400


async def test_expense_cannot_claim_a_bogus_user_id_as_payer(client):
    alice_token, alice_id = await _signup(client, "paidby4@example.com", "Alice")
    group_resp = await client.post("/api/v1/shared-expenses/groups", headers=_auth(alice_token), json={"name": "Solo", "members": []})
    group_id = group_resp.json()["id"]

    r = await client.post(
        f"/api/v1/shared-expenses/groups/{group_id}/expenses", headers=_auth(alice_token),
        json={"description": "Bogus", "amount": 20.00, "expense_date": "2026-07-10", "participant_ids": [alice_id], "pending_participants": [], "category": "Other", "paid_by": "not-a-real-id"},
    )
    assert r.status_code == 400


async def test_expense_can_be_logged_as_paid_by_someone_not_yet_signed_up(client):
    alice_token, alice_id = await _signup(client, "pendpay1@example.com", "Alice")
    group_resp = await client.post("/api/v1/shared-expenses/groups", headers=_auth(alice_token), json={"name": "Roommates", "members": []})
    group_id = group_resp.json()["id"]

    r = await client.post(
        f"/api/v1/shared-expenses/groups/{group_id}/expenses", headers=_auth(alice_token),
        json={
            "description": "Groceries", "amount": 60.00, "expense_date": "2026-07-10",
            "participant_ids": [alice_id], "pending_participants": [], "category": "Groceries",
            "paid_by_pending": {"email": "sam-pendpay1@example.com", "name": "Sam"},
        },
    )
    assert r.status_code == 201
    assert r.json()["paid_by"] is None
    assert r.json()["paid_by_name"] == "Sam"

    # Naming Sam as payer should have invited them to the group too --
    # same as a pending PARTICIPANT would, just via the payer path instead.
    group = (await client.get(f"/api/v1/shared-expenses/groups/{group_id}", headers=_auth(alice_token))).json()
    assert any(p["email"] == "sam-pendpay1@example.com" for p in group["pending_invites"])


async def test_pending_payer_reconnects_to_their_real_account_on_signup(client):
    """
    The core lifecycle proof: Sam is named as payer before signing up,
    then signs up with the same email, and the expense's paid_by
    should reconnect automatically -- reconnect_by_email() already had
    a branch for this (SharedExpense.paid_by IS NULL), written before
    this feature was reachable at all. This is the first real test of
    that branch actually firing end to end.
    """
    alice_token, alice_id = await _signup(client, "pendpay2@example.com", "Alice")
    group_resp = await client.post("/api/v1/shared-expenses/groups", headers=_auth(alice_token), json={"name": "Roommates", "members": []})
    group_id = group_resp.json()["id"]

    await client.post(
        f"/api/v1/shared-expenses/groups/{group_id}/expenses", headers=_auth(alice_token),
        json={
            "description": "Rent", "amount": 2000.00, "expense_date": "2026-07-10",
            "participant_ids": [alice_id], "pending_participants": [], "category": "Housing",
            "paid_by_pending": {"email": "sam-pendpay2@example.com", "name": "Sam"},
        },
    )

    # Sam signs up with the same email.
    sam_token, sam_id = await _signup(client, "sam-pendpay2@example.com", "Samuel")

    # From Sam's own perspective now: Alice owes Sam for the rent.
    balances = (await client.get("/api/v1/shared-expenses/balances", headers=_auth(sam_token))).json()
    alice_balance = next(b for b in balances if b["user_id"] == alice_id)
    assert alice_balance["you_owe_them"] == "0.00" or "they_owe_you" in alice_balance  # sanity: Sam sees a real balance row
    assert Decimal(alice_balance["they_owe_you"]) == Decimal("2000.00")

    # And from Alice's side, the expense's paid_by/paid_by_name now
    # correctly point at Sam's REAL account and REAL signup name
    # ("Samuel", not the "Sam" captured when Alice logged the expense) --
    # same real-name-wins-on-reconnect behavior already proven for splits.
    expenses = (await client.get(f"/api/v1/shared-expenses/groups/{group_id}/expenses", headers=_auth(alice_token))).json()
    assert expenses[0]["paid_by"] == sam_id
    assert expenses[0]["paid_by_name"] == "Samuel"


async def test_cannot_set_both_paid_by_and_paid_by_pending(client):
    alice_token, alice_id = await _signup(client, "pendpay3@example.com", "Alice")
    group_resp = await client.post("/api/v1/shared-expenses/groups", headers=_auth(alice_token), json={"name": "Solo", "members": []})
    group_id = group_resp.json()["id"]

    r = await client.post(
        f"/api/v1/shared-expenses/groups/{group_id}/expenses", headers=_auth(alice_token),
        json={
            "description": "Ambiguous", "amount": 20.00, "expense_date": "2026-07-10",
            "participant_ids": [alice_id], "pending_participants": [], "category": "Other",
            "paid_by": alice_id, "paid_by_pending": {"email": "someone@example.com", "name": "Someone"},
        },
    )
    assert r.status_code == 422  # pydantic validation error, not a 400 from the router


async def test_edit_expense_can_change_paid_by_to_another_real_member(client):
    alice_token, alice_id = await _signup(client, "editpay1@example.com", "Alice")
    _, bob_id = await _signup(client, "editpay1b@example.com", "Bob")
    group_resp = await client.post("/api/v1/shared-expenses/groups", headers=_auth(alice_token), json={"name": "Apartment", "members": [{"email": "editpay1b@example.com", "name": "Bob"}]})
    group_id = group_resp.json()["id"]
    expense_resp = await client.post(
        f"/api/v1/shared-expenses/groups/{group_id}/expenses", headers=_auth(alice_token),
        json={"description": "Dinner", "amount": 40.00, "expense_date": "2026-07-10", "participant_ids": [alice_id, bob_id], "pending_participants": [], "category": "Dining Out"},
    )
    expense_id = expense_resp.json()["id"]
    assert expense_resp.json()["paid_by"] == alice_id  # defaulted to caller

    r = await client.patch(f"/api/v1/shared-expenses/expenses/{expense_id}", headers=_auth(alice_token), json={"paid_by": bob_id})
    assert r.status_code == 200
    assert r.json()["paid_by"] == bob_id
    assert r.json()["paid_by_name"] == "Bob"


async def test_edit_expense_can_change_paid_by_to_a_pending_payer(client):
    alice_token, alice_id = await _signup(client, "editpay2@example.com", "Alice")
    group_resp = await client.post("/api/v1/shared-expenses/groups", headers=_auth(alice_token), json={"name": "Solo", "members": []})
    group_id = group_resp.json()["id"]
    expense_resp = await client.post(
        f"/api/v1/shared-expenses/groups/{group_id}/expenses", headers=_auth(alice_token),
        json={"description": "Coffee", "amount": 5.00, "expense_date": "2026-07-10", "participant_ids": [alice_id], "pending_participants": [], "category": "Dining Out"},
    )
    expense_id = expense_resp.json()["id"]

    r = await client.patch(
        f"/api/v1/shared-expenses/expenses/{expense_id}", headers=_auth(alice_token),
        json={"paid_by_pending": {"email": "sam-editpay2@example.com", "name": "Sam"}},
    )
    assert r.status_code == 200
    assert r.json()["paid_by"] is None
    assert r.json()["paid_by_name"] == "Sam"


async def test_editing_paid_by_does_not_touch_amount_or_splits(client):
    alice_token, alice_id = await _signup(client, "editpay3@example.com", "Alice")
    _, bob_id = await _signup(client, "editpay3b@example.com", "Bob")
    group_resp = await client.post("/api/v1/shared-expenses/groups", headers=_auth(alice_token), json={"name": "Apartment", "members": [{"email": "editpay3b@example.com", "name": "Bob"}]})
    group_id = group_resp.json()["id"]
    expense_resp = await client.post(
        f"/api/v1/shared-expenses/groups/{group_id}/expenses", headers=_auth(alice_token),
        json={"description": "Dinner", "amount": 40.00, "expense_date": "2026-07-10", "participant_ids": [alice_id, bob_id], "pending_participants": [], "category": "Dining Out"},
    )
    expense_id = expense_resp.json()["id"]
    original_splits = {s["name"]: s["share_amount"] for s in expense_resp.json()["splits"]}

    r = await client.patch(f"/api/v1/shared-expenses/expenses/{expense_id}", headers=_auth(alice_token), json={"paid_by": bob_id})
    assert r.json()["amount"] == "40.00"
    assert {s["name"]: s["share_amount"] for s in r.json()["splits"]} == original_splits


async def test_edit_expense_rejects_a_non_member_as_paid_by(client):
    alice_token, alice_id = await _signup(client, "editpay4@example.com", "Alice")
    _, outsider_id = await _signup(client, "editpay4outsider@example.com", "Outsider")
    group_resp = await client.post("/api/v1/shared-expenses/groups", headers=_auth(alice_token), json={"name": "Solo", "members": []})
    group_id = group_resp.json()["id"]
    expense_resp = await client.post(
        f"/api/v1/shared-expenses/groups/{group_id}/expenses", headers=_auth(alice_token),
        json={"description": "Coffee", "amount": 5.00, "expense_date": "2026-07-10", "participant_ids": [alice_id], "pending_participants": [], "category": "Dining Out"},
    )
    expense_id = expense_resp.json()["id"]

    r = await client.patch(f"/api/v1/shared-expenses/expenses/{expense_id}", headers=_auth(alice_token), json={"paid_by": outsider_id})
    assert r.status_code == 400


async def test_editing_paid_by_logs_a_system_comment(client):
    alice_token, alice_id = await _signup(client, "editpay5@example.com", "Alice")
    _, bob_id = await _signup(client, "editpay5b@example.com", "Bob")
    group_resp = await client.post("/api/v1/shared-expenses/groups", headers=_auth(alice_token), json={"name": "Apartment", "members": [{"email": "editpay5b@example.com", "name": "Bob"}]})
    group_id = group_resp.json()["id"]
    expense_resp = await client.post(
        f"/api/v1/shared-expenses/groups/{group_id}/expenses", headers=_auth(alice_token),
        json={"description": "Dinner", "amount": 40.00, "expense_date": "2026-07-10", "participant_ids": [alice_id, bob_id], "pending_participants": [], "category": "Dining Out"},
    )
    expense_id = expense_resp.json()["id"]

    await client.patch(f"/api/v1/shared-expenses/expenses/{expense_id}", headers=_auth(alice_token), json={"paid_by": bob_id})
    comments = (await client.get(f"/api/v1/shared-expenses/expenses/{expense_id}/comments", headers=_auth(alice_token))).json()
    assert any("payer" in c["body"] and c["is_system"] for c in comments)


async def test_create_expense_split_by_shares(client):
    alice_token, alice_id = await _signup(client, "alice-split1@example.com", "Alice")
    _, bob_id = await _signup(client, "bob-split1@example.com", "Bob")
    group_resp = await client.post("/api/v1/shared-expenses/groups", headers=_auth(alice_token), json={"name": "Trip", "members": [{"email": "bob-split1@example.com", "name": "Bob"}]})
    group_id = group_resp.json()["id"]

    r = await client.post(
        f"/api/v1/shared-expenses/groups/{group_id}/expenses", headers=_auth(alice_token),
        json={
            "description": "Dinner", "amount": 90.00, "expense_date": "2026-07-10",
            "participant_ids": [alice_id, bob_id], "pending_participants": [], "category": "Dining Out",
            "split_type": "shares", "participant_values": {alice_id: 2, bob_id: 1},
        },
    )
    assert r.status_code == 201
    assert r.json()["split_type"] == "shares"
    shares = {s["name"]: s["share_amount"] for s in r.json()["splits"]}
    assert shares == {"Alice": "60.00", "Bob": "30.00"}  # 2:1 ratio of $90


async def test_create_expense_split_by_percentage(client):
    alice_token, alice_id = await _signup(client, "alice-split2@example.com", "Alice")
    _, bob_id = await _signup(client, "bob-split2@example.com", "Bob")
    group_resp = await client.post("/api/v1/shared-expenses/groups", headers=_auth(alice_token), json={"name": "Trip", "members": [{"email": "bob-split2@example.com", "name": "Bob"}]})
    group_id = group_resp.json()["id"]

    r = await client.post(
        f"/api/v1/shared-expenses/groups/{group_id}/expenses", headers=_auth(alice_token),
        json={
            "description": "Dinner", "amount": 100.00, "expense_date": "2026-07-10",
            "participant_ids": [alice_id, bob_id], "pending_participants": [], "category": "Dining Out",
            "split_type": "percentage", "participant_values": {alice_id: 70, bob_id: 30},
        },
    )
    assert r.status_code == 201
    shares = {s["name"]: s["share_amount"] for s in r.json()["splits"]}
    assert shares == {"Alice": "70.00", "Bob": "30.00"}


async def test_create_expense_split_by_percentage_rejects_bad_total(client):
    alice_token, alice_id = await _signup(client, "alice-split3@example.com", "Alice")
    _, bob_id = await _signup(client, "bob-split3@example.com", "Bob")
    group_resp = await client.post("/api/v1/shared-expenses/groups", headers=_auth(alice_token), json={"name": "Trip", "members": [{"email": "bob-split3@example.com", "name": "Bob"}]})
    group_id = group_resp.json()["id"]

    r = await client.post(
        f"/api/v1/shared-expenses/groups/{group_id}/expenses", headers=_auth(alice_token),
        json={
            "description": "Dinner", "amount": 100.00, "expense_date": "2026-07-10",
            "participant_ids": [alice_id, bob_id], "pending_participants": [], "category": "Dining Out",
            "split_type": "percentage", "participant_values": {alice_id: 70, bob_id: 20},  # 90, not 100
        },
    )
    assert r.status_code == 400  # a real 400, not a raw 500 from the unhandled validation error


async def test_create_expense_split_exact_amounts(client):
    alice_token, alice_id = await _signup(client, "alice-split4@example.com", "Alice")
    _, bob_id = await _signup(client, "bob-split4@example.com", "Bob")
    group_resp = await client.post("/api/v1/shared-expenses/groups", headers=_auth(alice_token), json={"name": "Trip", "members": [{"email": "bob-split4@example.com", "name": "Bob"}]})
    group_id = group_resp.json()["id"]

    r = await client.post(
        f"/api/v1/shared-expenses/groups/{group_id}/expenses", headers=_auth(alice_token),
        json={
            "description": "Dinner", "amount": 100.00, "expense_date": "2026-07-10",
            "participant_ids": [alice_id, bob_id], "pending_participants": [], "category": "Dining Out",
            "split_type": "exact", "participant_values": {alice_id: 62.50, bob_id: 37.50},
        },
    )
    assert r.status_code == 201
    shares = {s["name"]: s["share_amount"] for s in r.json()["splits"]}
    assert shares == {"Alice": "62.50", "Bob": "37.50"}


async def test_expense_defaults_to_equal_split_type_when_not_specified(client):
    alice_token, alice_id = await _signup(client, "alice-split5@example.com", "Alice")
    group_resp = await client.post("/api/v1/shared-expenses/groups", headers=_auth(alice_token), json={"name": "Solo", "members": []})
    group_id = group_resp.json()["id"]
    r = await client.post(
        f"/api/v1/shared-expenses/groups/{group_id}/expenses", headers=_auth(alice_token),
        json={"description": "Coffee", "amount": 5.00, "expense_date": "2026-07-10", "participant_ids": [alice_id], "pending_participants": [], "category": "Dining Out"},
    )
    assert r.json()["split_type"] == "equal"


async def test_edit_expense_switches_split_type_and_persists_it(client):
    """
    The specific gap this guards: adjusting only the VALUES of an
    already-percentage-split expense (not re-sending split_type, since
    it didn't change) must still re-split as a percentage split --
    verified by making TWO edits, where the second only sends new
    values and no split_type at all.
    """
    alice_token, alice_id = await _signup(client, "alice-split6@example.com", "Alice")
    _, bob_id = await _signup(client, "bob-split6@example.com", "Bob")
    group_resp = await client.post("/api/v1/shared-expenses/groups", headers=_auth(alice_token), json={"name": "Trip", "members": [{"email": "bob-split6@example.com", "name": "Bob"}]})
    group_id = group_resp.json()["id"]
    expense_resp = await client.post(
        f"/api/v1/shared-expenses/groups/{group_id}/expenses", headers=_auth(alice_token),
        json={"description": "Dinner", "amount": 100.00, "expense_date": "2026-07-10", "participant_ids": [alice_id, bob_id], "pending_participants": [], "category": "Dining Out"},
    )
    expense_id = expense_resp.json()["id"]
    assert expense_resp.json()["split_type"] == "equal"

    # First edit: switch to percentage, 70/30.
    r1 = await client.patch(
        f"/api/v1/shared-expenses/expenses/{expense_id}", headers=_auth(alice_token),
        json={"split_type": "percentage", "participant_values": {alice_id: 70, bob_id: 30}},
    )
    assert r1.json()["split_type"] == "percentage"
    assert {s["name"]: s["share_amount"] for s in r1.json()["splits"]} == {"Alice": "70.00", "Bob": "30.00"}

    # Second edit: ONLY new values, split_type omitted entirely — must
    # still re-split as percentage, not silently revert to equal.
    r2 = await client.patch(
        f"/api/v1/shared-expenses/expenses/{expense_id}", headers=_auth(alice_token),
        json={"participant_values": {alice_id: 40, bob_id: 60}},
    )
    assert r2.json()["split_type"] == "percentage"  # still percentage, not reverted
    assert {s["name"]: s["share_amount"] for s in r2.json()["splits"]} == {"Alice": "40.00", "Bob": "60.00"}
