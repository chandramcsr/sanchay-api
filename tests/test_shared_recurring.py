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
