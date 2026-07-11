from datetime import datetime, timedelta, timezone


async def _signup(client, email, name):
    r = await client.post("/api/v1/auth/signup", json={"email": email, "password": "hunter2222", "display_name": name})
    body = r.json()
    return body["access_token"], body["user"]["id"]


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _days_ago(n: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=n)).strftime("%Y-%m-%d")


async def test_create_recurring_rule_with_start_date_today_materializes_immediately(client):
    """A rule whose first occurrence is due right now (start_date == today) should
    produce a real expense as part of the create call itself, not require a
    separate read to trigger materialization."""
    alice_token, alice_id = await _signup(client, "rec1@example.com", "Alice")
    _, bob_id = await _signup(client, "rec1b@example.com", "Bob")
    group_resp = await client.post("/api/v1/shared-expenses/groups", headers=_auth(alice_token), json={"name": "Apartment", "members": [{"email": "rec1b@example.com", "name": "Bob"}]})
    group_id = group_resp.json()["id"]

    r = await client.post(
        f"/api/v1/shared-expenses/groups/{group_id}/recurring", headers=_auth(alice_token),
        json={
            "description": "Rent", "amount": 2000.00, "category": "Housing",
            "participant_ids": [alice_id, bob_id], "pending_participants": [],
            "frequency": "monthly", "start_date": _today(),
        },
    )
    assert r.status_code == 201
    assert r.json()["last_materialized"] == _today()

    expenses = await client.get(f"/api/v1/shared-expenses/groups/{group_id}/expenses", headers=_auth(alice_token))
    assert len(expenses.json()) == 1
    assert expenses.json()[0]["description"] == "Rent"
    assert expenses.json()[0]["amount"] == "2000.00"


async def test_future_start_date_does_not_materialize_yet(client):
    alice_token, alice_id = await _signup(client, "rec2@example.com", "Alice")
    group_resp = await client.post("/api/v1/shared-expenses/groups", headers=_auth(alice_token), json={"name": "Solo", "members": []})
    group_id = group_resp.json()["id"]

    future_date = (datetime.now(timezone.utc) + timedelta(days=30)).strftime("%Y-%m-%d")
    r = await client.post(
        f"/api/v1/shared-expenses/groups/{group_id}/recurring", headers=_auth(alice_token),
        json={"description": "Future bill", "amount": 50.00, "participant_ids": [alice_id], "pending_participants": [], "frequency": "monthly", "start_date": future_date},
    )
    assert r.json()["last_materialized"] is None

    expenses = await client.get(f"/api/v1/shared-expenses/groups/{group_id}/expenses", headers=_auth(alice_token))
    assert expenses.json() == []


async def test_catch_up_materializes_every_missed_occurrence_on_next_read(client):
    """Simulates a rule that's gone unread for months -- start_date far enough in the
    past that, at monthly frequency, several occurrences are due at once."""
    alice_token, alice_id = await _signup(client, "rec3@example.com", "Alice")
    group_resp = await client.post("/api/v1/shared-expenses/groups", headers=_auth(alice_token), json={"name": "Solo", "members": []})
    group_id = group_resp.json()["id"]

    old_date = _days_ago(95)  # roughly 3 months back
    r = await client.post(
        f"/api/v1/shared-expenses/groups/{group_id}/recurring", headers=_auth(alice_token),
        json={"description": "Internet", "amount": 60.00, "participant_ids": [alice_id], "pending_participants": [], "frequency": "monthly", "start_date": old_date},
    )
    assert r.status_code == 201

    expenses = await client.get(f"/api/v1/shared-expenses/groups/{group_id}/expenses", headers=_auth(alice_token))
    # ~95 days at monthly cadence -> at least 3 occurrences (this month, last month, the one before)
    assert len(expenses.json()) >= 3
    assert all(e["description"] == "Internet" for e in expenses.json())


async def test_materializing_twice_in_a_row_does_not_duplicate(client):
    alice_token, alice_id = await _signup(client, "rec4@example.com", "Alice")
    group_resp = await client.post("/api/v1/shared-expenses/groups", headers=_auth(alice_token), json={"name": "Solo", "members": []})
    group_id = group_resp.json()["id"]

    await client.post(
        f"/api/v1/shared-expenses/groups/{group_id}/recurring", headers=_auth(alice_token),
        json={"description": "Netflix", "amount": 15.00, "participant_ids": [alice_id], "pending_participants": [], "frequency": "monthly", "start_date": _today()},
    )
    first_read = await client.get(f"/api/v1/shared-expenses/groups/{group_id}/expenses", headers=_auth(alice_token))
    second_read = await client.get(f"/api/v1/shared-expenses/groups/{group_id}/expenses", headers=_auth(alice_token))
    assert len(first_read.json()) == 1
    assert len(second_read.json()) == 1  # not 2 -- reading twice must not re-materialize the same occurrence


async def test_recurring_rule_splits_correctly_when_materialized(client):
    alice_token, alice_id = await _signup(client, "rec5@example.com", "Alice")
    _, bob_id = await _signup(client, "rec5b@example.com", "Bob")
    group_resp = await client.post("/api/v1/shared-expenses/groups", headers=_auth(alice_token), json={"name": "Apartment", "members": [{"email": "rec5b@example.com", "name": "Bob"}]})
    group_id = group_resp.json()["id"]

    await client.post(
        f"/api/v1/shared-expenses/groups/{group_id}/recurring", headers=_auth(alice_token),
        json={
            "description": "Rent", "amount": 2000.00, "split_type": "percentage",
            "participant_ids": [alice_id, bob_id], "pending_participants": [],
            "participant_values": {alice_id: 60, bob_id: 40},
            "frequency": "monthly", "start_date": _today(),
        },
    )
    expenses = (await client.get(f"/api/v1/shared-expenses/groups/{group_id}/expenses", headers=_auth(alice_token))).json()
    shares = {s["name"]: s["share_amount"] for s in expenses[0]["splits"]}
    assert shares == {"Alice": "1200.00", "Bob": "800.00"}


async def test_pausing_a_rule_stops_new_materialization(client):
    alice_token, alice_id = await _signup(client, "rec6@example.com", "Alice")
    group_resp = await client.post("/api/v1/shared-expenses/groups", headers=_auth(alice_token), json={"name": "Solo", "members": []})
    group_id = group_resp.json()["id"]

    old_date = _days_ago(40)
    create_resp = await client.post(
        f"/api/v1/shared-expenses/groups/{group_id}/recurring", headers=_auth(alice_token),
        json={"description": "Gym", "amount": 40.00, "participant_ids": [alice_id], "pending_participants": [], "frequency": "monthly", "start_date": old_date},
    )
    rule_id = create_resp.json()["id"]
    before_pause = (await client.get(f"/api/v1/shared-expenses/groups/{group_id}/expenses", headers=_auth(alice_token))).json()
    assert len(before_pause) >= 1

    pause_resp = await client.patch(f"/api/v1/shared-expenses/recurring/{rule_id}/active", headers=_auth(alice_token), json={"active": False})
    assert pause_resp.status_code == 200
    assert pause_resp.json()["active"] is False

    # Re-reading after pausing must not generate anything further, even
    # though there could still be "due" occurrences in principle.
    after_pause = (await client.get(f"/api/v1/shared-expenses/groups/{group_id}/expenses", headers=_auth(alice_token))).json()
    assert len(after_pause) == len(before_pause)


async def test_resuming_a_paused_rule_catches_up_again(client):
    alice_token, alice_id = await _signup(client, "rec7@example.com", "Alice")
    group_resp = await client.post("/api/v1/shared-expenses/groups", headers=_auth(alice_token), json={"name": "Solo", "members": []})
    group_id = group_resp.json()["id"]

    create_resp = await client.post(
        f"/api/v1/shared-expenses/groups/{group_id}/recurring", headers=_auth(alice_token),
        json={"description": "Storage unit", "amount": 80.00, "participant_ids": [alice_id], "pending_participants": [], "frequency": "monthly", "start_date": _today()},
    )
    rule_id = create_resp.json()["id"]
    await client.patch(f"/api/v1/shared-expenses/recurring/{rule_id}/active", headers=_auth(alice_token), json={"active": False})
    resume_resp = await client.patch(f"/api/v1/shared-expenses/recurring/{rule_id}/active", headers=_auth(alice_token), json={"active": True})
    assert resume_resp.json()["active"] is True


async def test_edit_recurring_rule_changes_amount_and_description(client):
    alice_token, alice_id = await _signup(client, "recedit1@example.com", "Alice")
    group_resp = await client.post("/api/v1/shared-expenses/groups", headers=_auth(alice_token), json={"name": "Solo", "members": []})
    group_id = group_resp.json()["id"]
    create_resp = await client.post(
        f"/api/v1/shared-expenses/groups/{group_id}/recurring", headers=_auth(alice_token),
        json={"description": "Gym", "amount": 40.00, "participant_ids": [alice_id], "pending_participants": [], "frequency": "monthly", "start_date": _today()},
    )
    rule_id = create_resp.json()["id"]

    r = await client.patch(
        f"/api/v1/shared-expenses/recurring/{rule_id}", headers=_auth(alice_token),
        json={"description": "Gym Membership", "amount": 45.00},
    )
    assert r.status_code == 200
    assert r.json()["description"] == "Gym Membership"
    assert r.json()["amount"] == "45.00"


async def test_edit_recurring_rule_never_changes_start_date(client):
    alice_token, alice_id = await _signup(client, "recedit2@example.com", "Alice")
    group_resp = await client.post("/api/v1/shared-expenses/groups", headers=_auth(alice_token), json={"name": "Solo", "members": []})
    group_id = group_resp.json()["id"]
    start = _today()
    create_resp = await client.post(
        f"/api/v1/shared-expenses/groups/{group_id}/recurring", headers=_auth(alice_token),
        json={"description": "Rent", "amount": 1000.00, "participant_ids": [alice_id], "pending_participants": [], "frequency": "monthly", "start_date": start},
    )
    rule_id = create_resp.json()["id"]

    r = await client.patch(f"/api/v1/shared-expenses/recurring/{rule_id}", headers=_auth(alice_token), json={"amount": 1100.00})
    assert r.json()["start_date"] == start  # untouched, even though amount changed


async def test_edit_recurring_rule_does_not_retroactively_change_already_materialized_expenses(client):
    alice_token, alice_id = await _signup(client, "recedit3@example.com", "Alice")
    group_resp = await client.post("/api/v1/shared-expenses/groups", headers=_auth(alice_token), json={"name": "Solo", "members": []})
    group_id = group_resp.json()["id"]
    create_resp = await client.post(
        f"/api/v1/shared-expenses/groups/{group_id}/recurring", headers=_auth(alice_token),
        json={"description": "Rent", "amount": 1000.00, "participant_ids": [alice_id], "pending_participants": [], "frequency": "monthly", "start_date": _today()},
    )
    rule_id = create_resp.json()["id"]
    before = (await client.get(f"/api/v1/shared-expenses/groups/{group_id}/expenses", headers=_auth(alice_token))).json()
    assert before[0]["amount"] == "1000.00"

    await client.patch(f"/api/v1/shared-expenses/recurring/{rule_id}", headers=_auth(alice_token), json={"amount": 1200.00})

    after = (await client.get(f"/api/v1/shared-expenses/groups/{group_id}/expenses", headers=_auth(alice_token))).json()
    assert after[0]["amount"] == "1000.00"  # the already-materialized expense is untouched
    assert after[0]["id"] == before[0]["id"]


async def test_edit_recurring_rule_can_change_paid_by_to_another_real_member(client):
    alice_token, alice_id = await _signup(client, "recedit4@example.com", "Alice")
    _, bob_id = await _signup(client, "recedit4b@example.com", "Bob")
    group_resp = await client.post("/api/v1/shared-expenses/groups", headers=_auth(alice_token), json={"name": "Apartment", "members": [{"email": "recedit4b@example.com", "name": "Bob"}]})
    group_id = group_resp.json()["id"]
    create_resp = await client.post(
        f"/api/v1/shared-expenses/groups/{group_id}/recurring", headers=_auth(alice_token),
        json={"description": "Rent", "amount": 1000.00, "participant_ids": [alice_id, bob_id], "pending_participants": [], "frequency": "monthly", "start_date": _today()},
    )
    rule_id = create_resp.json()["id"]

    r = await client.patch(f"/api/v1/shared-expenses/recurring/{rule_id}", headers=_auth(alice_token), json={"paid_by": bob_id})
    assert r.json()["created_by"] == bob_id
    assert r.json()["created_by_name"] == "Bob"


async def test_edit_recurring_rule_can_change_paid_by_to_a_pending_payer(client):
    alice_token, alice_id = await _signup(client, "recedit5@example.com", "Alice")
    group_resp = await client.post("/api/v1/shared-expenses/groups", headers=_auth(alice_token), json={"name": "Solo", "members": []})
    group_id = group_resp.json()["id"]
    create_resp = await client.post(
        f"/api/v1/shared-expenses/groups/{group_id}/recurring", headers=_auth(alice_token),
        json={"description": "Rent", "amount": 1000.00, "participant_ids": [alice_id], "pending_participants": [], "frequency": "monthly", "start_date": _today()},
    )
    rule_id = create_resp.json()["id"]

    r = await client.patch(
        f"/api/v1/shared-expenses/recurring/{rule_id}", headers=_auth(alice_token),
        json={"paid_by_pending": {"email": "sam-recedit5@example.com", "name": "Sam"}},
    )
    assert r.json()["created_by"] is None
    assert r.json()["created_by_name"] == "Sam"
    group = (await client.get(f"/api/v1/shared-expenses/groups/{group_id}", headers=_auth(alice_token))).json()
    assert any(p["email"] == "sam-recedit5@example.com" for p in group["pending_invites"])


async def test_edit_recurring_rule_can_clear_end_date(client):
    alice_token, alice_id = await _signup(client, "recedit6@example.com", "Alice")
    group_resp = await client.post("/api/v1/shared-expenses/groups", headers=_auth(alice_token), json={"name": "Solo", "members": []})
    group_id = group_resp.json()["id"]
    future_end = (datetime.now(timezone.utc) + timedelta(days=90)).strftime("%Y-%m-%d")
    create_resp = await client.post(
        f"/api/v1/shared-expenses/groups/{group_id}/recurring", headers=_auth(alice_token),
        json={"description": "Trial subscription", "amount": 10.00, "participant_ids": [alice_id], "pending_participants": [], "frequency": "monthly", "start_date": _today(), "end_date": future_end},
    )
    rule_id = create_resp.json()["id"]
    assert create_resp.json()["end_date"] == future_end

    r = await client.patch(f"/api/v1/shared-expenses/recurring/{rule_id}", headers=_auth(alice_token), json={"clear_end_date": True})
    assert r.json()["end_date"] is None


async def test_edit_recurring_rule_can_change_frequency(client):
    alice_token, alice_id = await _signup(client, "recedit7@example.com", "Alice")
    group_resp = await client.post("/api/v1/shared-expenses/groups", headers=_auth(alice_token), json={"name": "Solo", "members": []})
    group_id = group_resp.json()["id"]
    create_resp = await client.post(
        f"/api/v1/shared-expenses/groups/{group_id}/recurring", headers=_auth(alice_token),
        json={"description": "Insurance", "amount": 300.00, "participant_ids": [alice_id], "pending_participants": [], "frequency": "monthly", "start_date": _today()},
    )
    rule_id = create_resp.json()["id"]

    r = await client.patch(f"/api/v1/shared-expenses/recurring/{rule_id}", headers=_auth(alice_token), json={"frequency": "quarterly"})
    assert r.json()["frequency"] == "quarterly"


async def test_edit_recurring_rule_can_change_participants_and_split(client):
    alice_token, alice_id = await _signup(client, "recedit8@example.com", "Alice")
    _, bob_id = await _signup(client, "recedit8b@example.com", "Bob")
    group_resp = await client.post("/api/v1/shared-expenses/groups", headers=_auth(alice_token), json={"name": "Apartment", "members": [{"email": "recedit8b@example.com", "name": "Bob"}]})
    group_id = group_resp.json()["id"]
    create_resp = await client.post(
        f"/api/v1/shared-expenses/groups/{group_id}/recurring", headers=_auth(alice_token),
        json={"description": "Rent", "amount": 1000.00, "participant_ids": [alice_id], "pending_participants": [], "frequency": "monthly", "start_date": _today()},
    )
    rule_id = create_resp.json()["id"]

    r = await client.patch(
        f"/api/v1/shared-expenses/recurring/{rule_id}", headers=_auth(alice_token),
        json={
            "participant_ids": [alice_id, bob_id], "pending_participants": [],
            "split_type": "percentage", "participant_values": {alice_id: 70, bob_id: 30},
        },
    )
    assert r.status_code == 200
    assert r.json()["split_type"] == "percentage"


async def test_edit_recurring_rule_rejects_a_non_member_as_paid_by(client):
    alice_token, alice_id = await _signup(client, "recedit9@example.com", "Alice")
    _, outsider_id = await _signup(client, "recedit9outsider@example.com", "Outsider")
    group_resp = await client.post("/api/v1/shared-expenses/groups", headers=_auth(alice_token), json={"name": "Solo", "members": []})
    group_id = group_resp.json()["id"]
    create_resp = await client.post(
        f"/api/v1/shared-expenses/groups/{group_id}/recurring", headers=_auth(alice_token),
        json={"description": "Rent", "amount": 1000.00, "participant_ids": [alice_id], "pending_participants": [], "frequency": "monthly", "start_date": _today()},
    )
    rule_id = create_resp.json()["id"]

    r = await client.patch(f"/api/v1/shared-expenses/recurring/{rule_id}", headers=_auth(alice_token), json={"paid_by": outsider_id})
    assert r.status_code == 400


async def test_edit_recurring_rule_rejects_invalid_frequency(client):
    alice_token, alice_id = await _signup(client, "recedit10@example.com", "Alice")
    group_resp = await client.post("/api/v1/shared-expenses/groups", headers=_auth(alice_token), json={"name": "Solo", "members": []})
    group_id = group_resp.json()["id"]
    create_resp = await client.post(
        f"/api/v1/shared-expenses/groups/{group_id}/recurring", headers=_auth(alice_token),
        json={"description": "Rent", "amount": 1000.00, "participant_ids": [alice_id], "pending_participants": [], "frequency": "monthly", "start_date": _today()},
    )
    rule_id = create_resp.json()["id"]

    r = await client.patch(f"/api/v1/shared-expenses/recurring/{rule_id}", headers=_auth(alice_token), json={"frequency": "daily"})
    assert r.status_code == 422  # rejected by the schema's Literal type before it even reaches the service


async def test_edit_recurring_rule_requires_group_membership(client):
    alice_token, alice_id = await _signup(client, "recedit11@example.com", "Alice")
    mallory_token, _ = await _signup(client, "recedit11mallory@example.com", "Mallory")
    group_resp = await client.post("/api/v1/shared-expenses/groups", headers=_auth(alice_token), json={"name": "Private", "members": []})
    group_id = group_resp.json()["id"]
    create_resp = await client.post(
        f"/api/v1/shared-expenses/groups/{group_id}/recurring", headers=_auth(alice_token),
        json={"description": "Rent", "amount": 1000.00, "participant_ids": [alice_id], "pending_participants": [], "frequency": "monthly", "start_date": _today()},
    )
    rule_id = create_resp.json()["id"]

    r = await client.patch(f"/api/v1/shared-expenses/recurring/{rule_id}", headers=_auth(mallory_token), json={"amount": 1.00})
    assert r.status_code in (403, 404)


async def test_edit_nonexistent_recurring_rule_returns_404(client):
    alice_token, _ = await _signup(client, "recedit12@example.com", "Alice")
    r = await client.patch("/api/v1/shared-expenses/recurring/not-a-real-id", headers=_auth(alice_token), json={"amount": 1.00})
    assert r.status_code == 404


async def test_edit_recurring_rule_cannot_set_both_paid_by_fields(client):
    alice_token, alice_id = await _signup(client, "recedit13@example.com", "Alice")
    group_resp = await client.post("/api/v1/shared-expenses/groups", headers=_auth(alice_token), json={"name": "Solo", "members": []})
    group_id = group_resp.json()["id"]
    create_resp = await client.post(
        f"/api/v1/shared-expenses/groups/{group_id}/recurring", headers=_auth(alice_token),
        json={"description": "Rent", "amount": 1000.00, "participant_ids": [alice_id], "pending_participants": [], "frequency": "monthly", "start_date": _today()},
    )
    rule_id = create_resp.json()["id"]

    r = await client.patch(
        f"/api/v1/shared-expenses/recurring/{rule_id}", headers=_auth(alice_token),
        json={"paid_by": alice_id, "paid_by_pending": {"email": "x@example.com", "name": "X"}},
    )
    assert r.status_code == 422


async def test_quarterly_frequency_accepted_end_to_end(client):
    alice_token, alice_id = await _signup(client, "rec11@example.com", "Alice")
    group_resp = await client.post("/api/v1/shared-expenses/groups", headers=_auth(alice_token), json={"name": "Solo", "members": []})
    group_id = group_resp.json()["id"]

    r = await client.post(
        f"/api/v1/shared-expenses/groups/{group_id}/recurring", headers=_auth(alice_token),
        json={"description": "Car insurance", "amount": 450.00, "participant_ids": [alice_id], "pending_participants": [], "frequency": "quarterly", "start_date": _today()},
    )
    assert r.status_code == 201
    assert r.json()["frequency"] == "quarterly"
    assert r.json()["last_materialized"] == _today()


async def test_recurring_rule_paid_by_defaults_to_the_caller_when_omitted(client):
    alice_token, alice_id = await _signup(client, "rrpaid1@example.com", "Alice")
    group_resp = await client.post("/api/v1/shared-expenses/groups", headers=_auth(alice_token), json={"name": "Solo", "members": []})
    group_id = group_resp.json()["id"]
    r = await client.post(
        f"/api/v1/shared-expenses/groups/{group_id}/recurring", headers=_auth(alice_token),
        json={"description": "Rent", "amount": 1000.00, "participant_ids": [alice_id], "pending_participants": [], "frequency": "monthly", "start_date": _today()},
    )
    assert r.json()["created_by"] == alice_id
    assert r.json()["created_by_name"] == "Alice"


async def test_recurring_rule_can_be_set_up_as_paid_by_another_real_member(client):
    alice_token, alice_id = await _signup(client, "rrpaid2@example.com", "Alice")
    _, bob_id = await _signup(client, "rrpaid2b@example.com", "Bob")
    group_resp = await client.post("/api/v1/shared-expenses/groups", headers=_auth(alice_token), json={"name": "Apartment", "members": [{"email": "rrpaid2b@example.com", "name": "Bob"}]})
    group_id = group_resp.json()["id"]
    r = await client.post(
        f"/api/v1/shared-expenses/groups/{group_id}/recurring", headers=_auth(alice_token),
        json={
            "description": "Rent", "amount": 1000.00, "participant_ids": [alice_id, bob_id], "pending_participants": [],
            "frequency": "monthly", "start_date": _today(), "paid_by": bob_id,
        },
    )
    assert r.status_code == 201
    assert r.json()["created_by"] == bob_id
    assert r.json()["created_by_name"] == "Bob"
    # And the materialized occurrence should correctly attribute Bob as payer too.
    expenses = (await client.get(f"/api/v1/shared-expenses/groups/{group_id}/expenses", headers=_auth(alice_token))).json()
    assert expenses[0]["paid_by"] == bob_id


async def test_recurring_rule_can_be_set_up_as_paid_by_a_pending_payer(client):
    alice_token, alice_id = await _signup(client, "rrpaid3@example.com", "Alice")
    group_resp = await client.post("/api/v1/shared-expenses/groups", headers=_auth(alice_token), json={"name": "Solo", "members": []})
    group_id = group_resp.json()["id"]
    r = await client.post(
        f"/api/v1/shared-expenses/groups/{group_id}/recurring", headers=_auth(alice_token),
        json={
            "description": "Rent", "amount": 1000.00, "participant_ids": [alice_id], "pending_participants": [],
            "frequency": "monthly", "start_date": _today(), "paid_by_pending": {"email": "sam-rrpaid3@example.com", "name": "Sam"},
        },
    )
    assert r.status_code == 201
    assert r.json()["created_by"] is None
    assert r.json()["created_by_name"] == "Sam"

    group = (await client.get(f"/api/v1/shared-expenses/groups/{group_id}", headers=_auth(alice_token))).json()
    assert any(p["email"] == "sam-rrpaid3@example.com" for p in group["pending_invites"])


async def test_recurring_rule_pending_payer_reconnects_on_signup(client):
    alice_token, alice_id = await _signup(client, "rrpaid4@example.com", "Alice")
    group_resp = await client.post("/api/v1/shared-expenses/groups", headers=_auth(alice_token), json={"name": "Solo", "members": []})
    group_id = group_resp.json()["id"]
    await client.post(
        f"/api/v1/shared-expenses/groups/{group_id}/recurring", headers=_auth(alice_token),
        json={
            "description": "Rent", "amount": 1000.00, "participant_ids": [alice_id], "pending_participants": [],
            "frequency": "monthly", "start_date": _today(), "paid_by_pending": {"email": "sam-rrpaid4@example.com", "name": "Sam"},
        },
    )
    _, sam_id = await _signup(client, "sam-rrpaid4@example.com", "Samuel")

    rules = (await client.get(f"/api/v1/shared-expenses/groups/{group_id}/recurring", headers=_auth(alice_token))).json()
    assert rules[0]["created_by"] == sam_id
    assert rules[0]["created_by_name"] == "Samuel"


async def test_delete_recurring_rule(client):
    alice_token, alice_id = await _signup(client, "rrdel1@example.com", "Alice")
    group_resp = await client.post("/api/v1/shared-expenses/groups", headers=_auth(alice_token), json={"name": "Solo", "members": []})
    group_id = group_resp.json()["id"]
    create_resp = await client.post(
        f"/api/v1/shared-expenses/groups/{group_id}/recurring", headers=_auth(alice_token),
        json={"description": "Gym", "amount": 40.00, "participant_ids": [alice_id], "pending_participants": [], "frequency": "monthly", "start_date": _today()},
    )
    rule_id = create_resp.json()["id"]

    delete_resp = await client.delete(f"/api/v1/shared-expenses/recurring/{rule_id}", headers=_auth(alice_token))
    assert delete_resp.status_code == 204

    rules = (await client.get(f"/api/v1/shared-expenses/groups/{group_id}/recurring", headers=_auth(alice_token))).json()
    assert rules == []


async def test_deleting_a_recurring_rule_does_not_delete_expenses_it_already_materialized(client):
    alice_token, alice_id = await _signup(client, "rrdel2@example.com", "Alice")
    group_resp = await client.post("/api/v1/shared-expenses/groups", headers=_auth(alice_token), json={"name": "Solo", "members": []})
    group_id = group_resp.json()["id"]
    create_resp = await client.post(
        f"/api/v1/shared-expenses/groups/{group_id}/recurring", headers=_auth(alice_token),
        json={"description": "Gym", "amount": 40.00, "participant_ids": [alice_id], "pending_participants": [], "frequency": "monthly", "start_date": _today()},
    )
    rule_id = create_resp.json()["id"]
    before = (await client.get(f"/api/v1/shared-expenses/groups/{group_id}/expenses", headers=_auth(alice_token))).json()
    assert len(before) == 1  # materialized immediately since start_date is today

    await client.delete(f"/api/v1/shared-expenses/recurring/{rule_id}", headers=_auth(alice_token))

    after = (await client.get(f"/api/v1/shared-expenses/groups/{group_id}/expenses", headers=_auth(alice_token))).json()
    assert len(after) == 1  # still there, untouched


async def test_delete_recurring_rule_requires_group_membership(client):
    alice_token, alice_id = await _signup(client, "rrdel3@example.com", "Alice")
    mallory_token, _ = await _signup(client, "rrdel3mallory@example.com", "Mallory")
    group_resp = await client.post("/api/v1/shared-expenses/groups", headers=_auth(alice_token), json={"name": "Private", "members": []})
    group_id = group_resp.json()["id"]
    create_resp = await client.post(
        f"/api/v1/shared-expenses/groups/{group_id}/recurring", headers=_auth(alice_token),
        json={"description": "Rent", "amount": 1000.00, "participant_ids": [alice_id], "pending_participants": [], "frequency": "monthly", "start_date": _today()},
    )
    rule_id = create_resp.json()["id"]

    r = await client.delete(f"/api/v1/shared-expenses/recurring/{rule_id}", headers=_auth(mallory_token))
    assert r.status_code in (403, 404)


async def test_delete_nonexistent_recurring_rule_returns_404(client):
    alice_token, _ = await _signup(client, "rrdel4@example.com", "Alice")
    r = await client.delete("/api/v1/shared-expenses/recurring/not-a-real-id", headers=_auth(alice_token))
    assert r.status_code == 404


async def test_recurring_rule_output_exposes_participants_and_split_for_editing(client):
    """
    Confirms the fields an edit form actually needs to pre-fill correctly
    -- previously RecurringRuleOut only exposed schedule metadata
    (frequency, dates, amount), with no way for a client to know who was
    even in the split, let alone edit it accurately.
    """
    alice_token, alice_id = await _signup(client, "rrfields1@example.com", "Alice")
    _, bob_id = await _signup(client, "rrfields1b@example.com", "Bob")
    group_resp = await client.post("/api/v1/shared-expenses/groups", headers=_auth(alice_token), json={"name": "Apartment", "members": [{"email": "rrfields1b@example.com", "name": "Bob"}]})
    group_id = group_resp.json()["id"]
    create_resp = await client.post(
        f"/api/v1/shared-expenses/groups/{group_id}/recurring", headers=_auth(alice_token),
        json={
            "description": "Rent", "amount": 1000.00, "participant_ids": [alice_id, bob_id], "pending_participants": [],
            "split_type": "percentage", "participant_values": {alice_id: 60, bob_id: 40},
            "frequency": "monthly", "start_date": _today(),
        },
    )
    body = create_resp.json()
    assert set(body["participant_ids"]) == {alice_id, bob_id}
    assert body["pending_participants"] == []
    assert body["split_type"] == "percentage"
    assert body["participant_values"][alice_id] == "60.00"
    assert body["participant_values"][bob_id] == "40.00"

    # Also confirmed on the LIST endpoint, not just the create response.
    rules = (await client.get(f"/api/v1/shared-expenses/groups/{group_id}/recurring", headers=_auth(alice_token))).json()
    assert set(rules[0]["participant_ids"]) == {alice_id, bob_id}


async def test_list_recurring_rules_for_a_group(client):
    alice_token, alice_id = await _signup(client, "rec8@example.com", "Alice")
    group_resp = await client.post("/api/v1/shared-expenses/groups", headers=_auth(alice_token), json={"name": "Solo", "members": []})
    group_id = group_resp.json()["id"]

    await client.post(
        f"/api/v1/shared-expenses/groups/{group_id}/recurring", headers=_auth(alice_token),
        json={"description": "Rent", "amount": 2000.00, "participant_ids": [alice_id], "pending_participants": [], "frequency": "monthly", "start_date": _today()},
    )
    await client.post(
        f"/api/v1/shared-expenses/groups/{group_id}/recurring", headers=_auth(alice_token),
        json={"description": "Internet", "amount": 60.00, "participant_ids": [alice_id], "pending_participants": [], "frequency": "monthly", "start_date": _today()},
    )
    rules = (await client.get(f"/api/v1/shared-expenses/groups/{group_id}/recurring", headers=_auth(alice_token))).json()
    assert {r["description"] for r in rules} == {"Rent", "Internet"}


async def test_non_member_cannot_create_or_view_recurring_rules(client):
    alice_token, alice_id = await _signup(client, "rec9@example.com", "Alice")
    mallory_token, _ = await _signup(client, "rec9mallory@example.com", "Mallory")
    group_resp = await client.post("/api/v1/shared-expenses/groups", headers=_auth(alice_token), json={"name": "Private", "members": []})
    group_id = group_resp.json()["id"]

    create_attempt = await client.post(
        f"/api/v1/shared-expenses/groups/{group_id}/recurring", headers=_auth(mallory_token),
        json={"description": "Sneaky", "amount": 10.00, "participant_ids": [alice_id], "pending_participants": [], "frequency": "monthly", "start_date": _today()},
    )
    assert create_attempt.status_code in (403, 404)

    list_attempt = await client.get(f"/api/v1/shared-expenses/groups/{group_id}/recurring", headers=_auth(mallory_token))
    assert list_attempt.status_code in (403, 404)


async def test_balances_endpoint_also_triggers_materialization(client):
    """The /balances endpoint spans ALL of a user's groups, not one --
    confirms materialization runs there too, not just on the per-group
    expenses list."""
    alice_token, alice_id = await _signup(client, "rec10@example.com", "Alice")
    _, bob_id = await _signup(client, "rec10b@example.com", "Bob")
    group_resp = await client.post("/api/v1/shared-expenses/groups", headers=_auth(alice_token), json={"name": "Apartment", "members": [{"email": "rec10b@example.com", "name": "Bob"}]})
    group_id = group_resp.json()["id"]

    await client.post(
        f"/api/v1/shared-expenses/groups/{group_id}/recurring", headers=_auth(alice_token),
        json={
            "description": "Rent", "amount": 1000.00,
            "participant_ids": [alice_id, bob_id], "pending_participants": [],
            "frequency": "monthly", "start_date": _today(),
        },
    )
    # Bob reads /balances (not the group's own expenses endpoint) --
    # should still see the materialized rent split reflected.
    bob_login = await client.post("/api/v1/auth/login", json={"email": "rec10b@example.com", "password": "hunter2222"})
    bob_token = bob_login.json()["access_token"]
    balances = (await client.get("/api/v1/shared-expenses/balances", headers=_auth(bob_token))).json()
    alice_balance = next(b for b in balances if b["name"] == "Alice")
    assert alice_balance["you_owe_them"] == "500.00"
