from decimal import Decimal

import pytest
from sqlalchemy import select

from app.core.security import hash_password
from app.models.group_member import GroupMember
from app.models.shared_expense_comment import SharedExpenseComment
from app.models.shared_expense_split import SharedExpenseSplit
from app.models.user import User
from app.services.shared_expense_service import (
    add_comment,
    compute_balance,
    compute_balance_with_frozen_friend,
    compute_group_debt_simplification,
    create_group,
    create_shared_expense,
    edit_shared_expense,
    ensure_pending_invite,
    freeze_user_references,
    get_all_balances,
    get_balance_breakdown,
    get_settlements_received,
    record_settlement,
    split_by_percentage,
    split_by_shares,
    split_evenly,
    split_exact,
    SplitValidationError,
)


# ---------- split_evenly: pure, synchronous, no DB ----------

def test_divides_cleanly_when_it_divides_cleanly():
    shares = split_evenly(Decimal("120.00"), ["a", "b", "c"])
    assert shares == {"a": Decimal("40.00"), "b": Decimal("40.00"), "c": Decimal("40.00")}


def test_the_property_that_actually_matters_sum_always_equals_total():
    # $100 / 3 doesn't divide evenly — the one thing that must never
    # be false: the parts sum EXACTLY to the total, or the group
    # balance can never truly reach zero.
    shares = split_evenly(Decimal("100.00"), ["a", "b", "c"])
    assert sum(shares.values()) == Decimal("100.00")


def test_extra_cents_distributed_one_at_a_time_not_all_to_one_person():
    shares = split_evenly(Decimal("100.00"), ["a", "b", "c"])
    values = sorted(shares.values())
    assert values == [Decimal("33.33"), Decimal("33.33"), Decimal("33.34")]


def test_deterministic_not_random_same_input_same_output_every_time():
    # "Doesn't matter who gets the cent" was answered as "not
    # important which specific person" — NOT as "nondeterministic."
    results = [split_evenly(Decimal("100.00"), ["a", "b", "c"]) for _ in range(20)]
    assert all(r == results[0] for r in results)


def test_single_participant_gets_the_whole_amount():
    assert split_evenly(Decimal("58.47"), ["a"]) == {"a": Decimal("58.47")}


def test_many_participants_still_sums_exactly():
    ids = [f"p{i}" for i in range(7)]
    shares = split_evenly(Decimal("50.00"), ids)
    assert sum(shares.values()) == Decimal("50.00")
    assert len(shares) == 7


def test_empty_participant_list_returns_empty_without_crashing():
    assert split_evenly(Decimal("100.00"), []) == {}


def test_zero_amount_splits_to_zero_for_everyone():
    assert split_evenly(Decimal("0.00"), ["a", "b"]) == {"a": Decimal("0.00"), "b": Decimal("0.00")}


# ---------- split_by_shares ----------

def test_shares_split_proportionally_not_evenly():
    # Alice gets 2 shares, Bob gets 1 — Alice owes twice what Bob does.
    shares = split_by_shares(Decimal("90.00"), {"alice": Decimal("2"), "bob": Decimal("1")})
    assert shares == {"alice": Decimal("60.00"), "bob": Decimal("30.00")}


def test_shares_still_sum_to_total_when_it_does_not_divide_cleanly():
    shares = split_by_shares(Decimal("100.00"), {"alice": Decimal("2"), "bob": Decimal("1")})
    assert sum(shares.values()) == Decimal("100.00")


def test_a_zero_share_is_allowed_and_gets_nothing():
    shares = split_by_shares(Decimal("60.00"), {"alice": Decimal("1"), "bob": Decimal("1"), "carol": Decimal("0")})
    assert shares["carol"] == Decimal("0.00")
    assert shares["alice"] == Decimal("30.00")
    assert shares["bob"] == Decimal("30.00")


def test_equal_shares_produce_the_same_result_as_split_evenly():
    equal = split_evenly(Decimal("100.00"), ["a", "b", "c"])
    via_shares = split_by_shares(Decimal("100.00"), {"a": Decimal("1"), "b": Decimal("1"), "c": Decimal("1")})
    assert equal == via_shares


def test_all_zero_shares_is_rejected():
    with pytest.raises(SplitValidationError):
        split_by_shares(Decimal("50.00"), {"a": Decimal("0"), "b": Decimal("0")})


# ---------- split_by_percentage ----------

def test_percentage_split_divides_proportionally():
    shares = split_by_percentage(Decimal("100.00"), {"alice": Decimal("60"), "bob": Decimal("40")})
    assert shares == {"alice": Decimal("60.00"), "bob": Decimal("40.00")}


def test_percentages_must_sum_to_exactly_100():
    with pytest.raises(SplitValidationError):
        split_by_percentage(Decimal("100.00"), {"alice": Decimal("60"), "bob": Decimal("39")})  # 99, not 100


def test_percentages_summing_to_over_100_is_also_rejected():
    with pytest.raises(SplitValidationError):
        split_by_percentage(Decimal("100.00"), {"alice": Decimal("60"), "bob": Decimal("50")})  # 110


def test_uneven_percentage_split_still_sums_to_the_real_total():
    shares = split_by_percentage(Decimal("33.33"), {"alice": Decimal("33.33"), "bob": Decimal("33.33"), "carol": Decimal("33.34")})
    assert sum(shares.values()) == Decimal("33.33")


# ---------- split_exact ----------

def test_exact_split_uses_the_given_amounts_directly():
    shares = split_exact(Decimal("100.00"), {"alice": Decimal("70.00"), "bob": Decimal("30.00")})
    assert shares == {"alice": Decimal("70.00"), "bob": Decimal("30.00")}


def test_exact_amounts_must_sum_to_the_real_total():
    with pytest.raises(SplitValidationError):
        split_exact(Decimal("100.00"), {"alice": Decimal("70.00"), "bob": Decimal("25.00")})  # 95, not 100


def test_exact_amounts_over_the_total_is_also_rejected():
    with pytest.raises(SplitValidationError):
        split_exact(Decimal("100.00"), {"alice": Decimal("70.00"), "bob": Decimal("35.00")})  # 105


# ---------- create_shared_expense / compute_balance ----------

async def _make_users(db_session, suffix=""):
    alice = User(email=f"alice{suffix}@example.com", hashed_password=hash_password("hunter2222"), display_name="Alice")
    bob = User(email=f"bob{suffix}@example.com", hashed_password=hash_password("hunter2222"), display_name="Bob")
    db_session.add(alice)
    db_session.add(bob)
    await db_session.commit()
    await db_session.refresh(alice)
    await db_session.refresh(bob)
    return alice, bob


async def test_alice_pays_bob_owes_his_share(db_session):
    alice, bob = await _make_users(db_session, "1")
    group = await create_group(db_session, name="Roommates", created_by=alice.id, member_ids=[bob.id])
    await create_shared_expense(
        db_session, group_id=group.id, paid_by=alice.id, description="Dinner",
        amount=Decimal("100.00"), expense_date="2026-07-08", participant_ids=[alice.id, bob.id],
    )
    balance = await compute_balance(db_session, user_a=bob.id, user_b=alice.id)
    assert balance == Decimal("50.00")
    reverse = await compute_balance(db_session, user_a=alice.id, user_b=bob.id)
    assert reverse == Decimal("-50.00")


async def test_settlement_reduces_the_balance(db_session):
    alice, bob = await _make_users(db_session, "2")
    group = await create_group(db_session, name="Roommates", created_by=alice.id, member_ids=[bob.id])
    await create_shared_expense(
        db_session, group_id=group.id, paid_by=alice.id, description="Dinner",
        amount=Decimal("100.00"), expense_date="2026-07-08", participant_ids=[alice.id, bob.id],
    )
    await record_settlement(db_session, from_user_id=bob.id, to_user_id=alice.id, amount=Decimal("50.00"), settled_date="2026-07-09")
    balance = await compute_balance(db_session, user_a=bob.id, user_b=alice.id)
    assert balance == Decimal("0.00")


async def test_editing_the_amount_corrects_both_the_shared_record_and_the_balance(db_session):
    alice, bob = await _make_users(db_session, "3")
    group = await create_group(db_session, name="Roommates", created_by=alice.id, member_ids=[bob.id])
    expense = await create_shared_expense(
        db_session, group_id=group.id, paid_by=alice.id, description="Dinner",
        amount=Decimal("100.00"), expense_date="2026-07-08", participant_ids=[alice.id, bob.id],
    )
    await edit_shared_expense(db_session, expense_id=expense.id, edited_by=bob.id, new_amount=Decimal("80.00"))
    balance = await compute_balance(db_session, user_a=bob.id, user_b=alice.id)
    assert balance == Decimal("40.00")  # re-split from the NEW total, not the old one


async def test_editing_logs_a_system_comment_with_the_actual_change(db_session):
    alice, bob = await _make_users(db_session, "4")
    group = await create_group(db_session, name="Roommates", created_by=alice.id, member_ids=[bob.id])
    expense = await create_shared_expense(
        db_session, group_id=group.id, paid_by=alice.id, description="Dinner",
        amount=Decimal("100.00"), expense_date="2026-07-08", participant_ids=[alice.id, bob.id],
    )
    await edit_shared_expense(db_session, expense_id=expense.id, edited_by=bob.id, new_amount=Decimal("80.00"))

    result = await db_session.execute(select(SharedExpenseComment).where(SharedExpenseComment.shared_expense_id == expense.id))
    comments = list(result.scalars().all())
    assert len(comments) == 1
    assert comments[0].is_system is True
    assert "100.00" in comments[0].body and "80.00" in comments[0].body


async def test_regular_comment_is_not_flagged_as_system(db_session):
    alice, bob = await _make_users(db_session, "5")
    group = await create_group(db_session, name="Roommates", created_by=alice.id, member_ids=[bob.id])
    expense = await create_shared_expense(
        db_session, group_id=group.id, paid_by=alice.id, description="Dinner",
        amount=Decimal("100.00"), expense_date="2026-07-08", participant_ids=[alice.id, bob.id],
    )
    comment = await add_comment(db_session, expense_id=expense.id, user_id=bob.id, body="I wasn't even there!")
    assert comment.is_system is False
    assert comment.body == "I wasn't even there!"


# ---------- freeze_user_references (the account-deletion policy) ----------

async def test_freezing_nulls_the_user_reference_but_keeps_the_name_and_balance(db_session):
    alice, bob = await _make_users(db_session, "6")
    group = await create_group(db_session, name="Roommates", created_by=alice.id, member_ids=[bob.id])
    await create_shared_expense(
        db_session, group_id=group.id, paid_by=alice.id, description="Dinner",
        amount=Decimal("100.00"), expense_date="2026-07-08", participant_ids=[alice.id, bob.id],
    )

    await freeze_user_references(db_session, user_id=bob.id)

    result = await db_session.execute(select(SharedExpenseSplit).where(SharedExpenseSplit.name_snapshot == "Bob"))
    splits = list(result.scalars().all())
    assert len(splits) == 1
    assert splits[0].user_id is None  # reference gone
    assert splits[0].name_snapshot == "Bob"  # name survives
    assert splits[0].share_amount == Decimal("50.00")  # the actual debt amount survives too


async def test_frozen_expense_still_shows_the_payer_name_even_though_the_account_is_gone(db_session):
    alice, bob = await _make_users(db_session, "7")
    group = await create_group(db_session, name="Roommates", created_by=alice.id, member_ids=[bob.id])
    expense = await create_shared_expense(
        db_session, group_id=group.id, paid_by=alice.id, description="Dinner",
        amount=Decimal("100.00"), expense_date="2026-07-08", participant_ids=[alice.id, bob.id],
    )

    await freeze_user_references(db_session, user_id=alice.id)

    await db_session.refresh(expense)
    assert expense.paid_by is None
    assert expense.paid_by_name_snapshot == "Alice"


# ---------- the real integration: does delete_account actually freeze, not cascade? ----------

async def test_delete_account_freezes_shared_expense_history_instead_of_destroying_it(db_session):
    from app.services import auth_service

    alice, bob = await _make_users(db_session, "8")
    group = await create_group(db_session, name="Roommates", created_by=alice.id, member_ids=[bob.id])
    await create_shared_expense(
        db_session, group_id=group.id, paid_by=alice.id, description="Dinner",
        amount=Decimal("100.00"), expense_date="2026-07-08", participant_ids=[alice.id, bob.id],
    )

    # Bob deletes HIS account — Alice's view of the shared history must survive.
    await auth_service.delete_account(db_session, current_user=bob, password="hunter2222")

    result = await db_session.execute(select(SharedExpenseSplit).where(SharedExpenseSplit.name_snapshot == "Bob"))
    splits = list(result.scalars().all())
    assert len(splits) == 1
    assert splits[0].user_id is None
    assert splits[0].share_amount == Decimal("50.00")  # the debt itself is untouched

    # And Bob's actual account is genuinely gone.
    from app.repositories import user_repository
    assert await user_repository.get_by_id(db_session, bob.id) is None


# ---------- email-based reconnection ----------

async def test_signing_up_again_with_the_same_email_reconnects_frozen_history(db_session):
    from app.services import auth_service
    from fastapi import BackgroundTasks

    alice, bob = await _make_users(db_session, "9")
    group = await create_group(db_session, name="Roommates", created_by=alice.id, member_ids=[bob.id])
    await create_shared_expense(
        db_session, group_id=group.id, paid_by=alice.id, description="Dinner",
        amount=Decimal("100.00"), expense_date="2026-07-08", participant_ids=[alice.id, bob.id],
    )
    bob_email = bob.email
    await auth_service.delete_account(db_session, current_user=bob, password="hunter2222")

    # Confirm it's genuinely frozen first.
    result = await db_session.execute(select(SharedExpenseSplit).where(SharedExpenseSplit.name_snapshot == "Bob"))
    assert result.scalar_one().user_id is None

    # Bob signs up again with the SAME email.
    _, _, new_bob, reconnect_summary, _joined = await auth_service.signup(
        db_session, BackgroundTasks(), email=bob_email, password="newpassword1", display_name="Bob"
    )

    assert reconnect_summary["groups_reconnected"] == 1
    assert reconnect_summary["total_amount"] == Decimal("50.00")

    result = await db_session.execute(select(SharedExpenseSplit).where(SharedExpenseSplit.name_snapshot == "Bob"))
    reconnected_split = result.scalar_one()
    assert reconnected_split.user_id == new_bob.id  # relinked to the NEW account

    # And the balance is live and correct again, computed against the new user id.
    balance = await compute_balance(db_session, user_a=new_bob.id, user_b=alice.id)
    assert balance == Decimal("50.00")


async def test_signing_up_with_a_different_email_reconnects_nothing(db_session):
    from app.services import auth_service
    from fastapi import BackgroundTasks

    alice, bob = await _make_users(db_session, "10")
    group = await create_group(db_session, name="Roommates", created_by=alice.id, member_ids=[bob.id])
    await create_shared_expense(
        db_session, group_id=group.id, paid_by=alice.id, description="Dinner",
        amount=Decimal("100.00"), expense_date="2026-07-08", participant_ids=[alice.id, bob.id],
    )
    await auth_service.delete_account(db_session, current_user=bob, password="hunter2222")

    # A completely unrelated signup must not pick up Bob's frozen history.
    _, _, _stranger, reconnect_summary, _joined = await auth_service.signup(
        db_session, BackgroundTasks(), email="totally-unrelated@example.com", password="hunter2222", display_name="Stranger"
    )
    assert reconnect_summary["groups_reconnected"] == 0
    assert reconnect_summary["total_amount"] == Decimal("0.00")


async def test_a_brand_new_signup_with_no_prior_history_reconnects_nothing(db_session):
    from app.services import auth_service
    from fastapi import BackgroundTasks

    _, _, _user, reconnect_summary, _joined = await auth_service.signup(
        db_session, BackgroundTasks(), email="brand-new-11@example.com", password="hunter2222", display_name="New"
    )
    assert reconnect_summary["groups_reconnected"] == 0
    assert reconnect_summary["total_amount"] == Decimal("0.00")


async def test_reconnection_is_visible_in_the_real_signup_api_response(client):
    from app.services import auth_service
    from fastapi import BackgroundTasks

    # Set up frozen history using the service layer directly (simpler
    # than going through the full HTTP group-creation flow, which
    # doesn't exist yet — phase 2). Uses the SAME db the client's
    # overridden get_db() resolves to, per this test's fixtures.
    from tests.conftest import TestingSessionLocal
    async with TestingSessionLocal() as db:
        alice, bob = await _make_users(db, "12")
        group = await create_group(db, name="Roommates", created_by=alice.id, member_ids=[bob.id])
        await create_shared_expense(
            db, group_id=group.id, paid_by=alice.id, description="Dinner",
            amount=Decimal("100.00"), expense_date="2026-07-08", participant_ids=[alice.id, bob.id],
        )
        bob_email = bob.email
        await auth_service.delete_account(db, current_user=bob, password="hunter2222")

    r = await client.post("/api/v1/auth/signup", json={"email": bob_email, "password": "newpassword1", "display_name": "Bob"})
    assert r.status_code == 201
    body = r.json()
    assert body["reconnected_history"] is not None
    assert body["reconnected_history"]["groups_reconnected"] == 1
    assert body["reconnected_history"]["total_amount"] == "50.00"


async def test_normal_signup_has_no_reconnected_history_field_populated(client):
    r = await client.post("/api/v1/auth/signup", json={"email": "nothing-special@example.com", "password": "hunter2222", "display_name": "Plain"})
    assert r.status_code == 201
    assert r.json()["reconnected_history"] is None


async def test_reconnection_updates_the_name_to_the_real_signup_name_not_the_frozen_one(db_session):
    """
    The real bug this guards against: Bob's account is frozen with
    name_snapshot="Bob", but he signs back up as "Robert" (people
    change how they present their name all the time). Every reconnected
    row must show "Robert" now, consistently -- not stay frozen at
    "Bob" while OTHER reconnected data (like group membership) shows
    "Robert", which was the actual inconsistency reported.
    """
    from app.services import auth_service
    from fastapi import BackgroundTasks

    alice, bob = await _make_users(db_session, "13")
    group = await create_group(db_session, name="Roommates", created_by=alice.id, member_ids=[bob.id])
    await create_shared_expense(
        db_session, group_id=group.id, paid_by=alice.id, description="Dinner",
        amount=Decimal("100.00"), expense_date="2026-07-08", participant_ids=[alice.id, bob.id],
    )
    bob_email = bob.email
    await auth_service.delete_account(db_session, current_user=bob, password="hunter2222")

    _, _, new_bob, _reconnect, _joined = await auth_service.signup(
        db_session, BackgroundTasks(), email=bob_email, password="newpassword1", display_name="Robert"
    )

    result = await db_session.execute(select(SharedExpenseSplit).where(SharedExpenseSplit.user_id == new_bob.id))
    split = result.scalar_one()
    assert split.name_snapshot == "Robert"  # the REAL current name, not frozen "Bob"

    result = await db_session.execute(select(GroupMember).where(GroupMember.user_id == new_bob.id))
    member = result.scalar_one()
    assert member.name_snapshot == "Robert"  # consistent with the split above -- this was the actual bug


async def test_a_pending_invite_name_is_replaced_by_the_real_signup_name_consistently(db_session):
    """
    Same consistency guarantee, for the OTHER path into this
    mechanism: someone invited by name (before having any account at
    all) who then signs up with a different name than whoever invited
    them typed in. The invite name was only ever a placeholder.
    """
    from app.services import auth_service
    from fastapi import BackgroundTasks

    alice, _bob_unused = await _make_users(db_session, "14")
    group = await create_group(db_session, name="Roommates", created_by=alice.id, member_ids=[])
    # The router normally calls ensure_pending_invite before
    # create_shared_expense — calling the service function directly
    # here, so setting that up explicitly rather than going through
    # the HTTP layer for this test.
    await ensure_pending_invite(
        db_session, BackgroundTasks(), group_id=group.id, email="sam-recon14@example.com", name="Sammy",
        invited_by=alice.id, group_name=group.name, frontend_url="https://example.com",
    )
    await create_shared_expense(
        db_session, group_id=group.id, paid_by=alice.id, description="Dinner",
        amount=Decimal("100.00"), expense_date="2026-07-08", participant_ids=[alice.id],
        pending_participants=[{"email": "sam-recon14@example.com", "name": "Sammy"}],
    )

    _, _, sam, _reconnect, joined = await auth_service.signup(
        db_session, BackgroundTasks(), email="sam-recon14@example.com", password="hunter2222", display_name="Samuel"
    )
    assert joined == ["Roommates"]

    result = await db_session.execute(select(SharedExpenseSplit).where(SharedExpenseSplit.user_id == sam.id))
    split = result.scalar_one()
    assert split.name_snapshot == "Samuel"  # the real signup name, not the invite-time "Sammy"

    result = await db_session.execute(select(GroupMember).where(GroupMember.user_id == sam.id))
    member = result.scalar_one()
    assert member.name_snapshot == "Samuel"  # consistent with the split


# ---------- frozen friends showing up in the top-level balance summary ----------
#
# The gap this closes: get_all_balances() used to only ever look at
# LIVE group members (matched by user_id) -- a friend whose account
# had since been deleted was invisible in this top-level summary even
# though their historical expenses stayed visible inside the group's
# own detail view. Fixed by also matching frozen friends via
# email_ref, the same durable identity anchor freeze_user_references
# already relies on for reconnect_by_email.


async def test_compute_balance_with_frozen_friend_reflects_the_real_debt(db_session):
    from app.services.shared_expense_service import email_reference

    alice, bob = await _make_users(db_session, "-frozen1")
    group = await create_group(db_session, name="Roommates", created_by=alice.id, member_ids=[bob.id])
    await create_shared_expense(
        db_session, group_id=group.id, paid_by=alice.id, description="Dinner",
        amount=Decimal("100.00"), expense_date="2026-07-08", participant_ids=[alice.id, bob.id],
    )
    ref = email_reference(bob.email)
    await freeze_user_references(db_session, user_id=bob.id)

    balance = await compute_balance_with_frozen_friend(db_session, user_id=alice.id, friend_email_ref=ref)
    assert balance == Decimal("-50.00")  # negative = the frozen friend (Bob) owes Alice


async def test_get_all_balances_includes_a_frozen_friend(db_session):
    alice, bob = await _make_users(db_session, "-frozen2")
    group = await create_group(db_session, name="Roommates", created_by=alice.id, member_ids=[bob.id])
    await create_shared_expense(
        db_session, group_id=group.id, paid_by=alice.id, description="Dinner",
        amount=Decimal("100.00"), expense_date="2026-07-08", participant_ids=[alice.id, bob.id],
    )
    await freeze_user_references(db_session, user_id=bob.id)

    balances = await get_all_balances(db_session, user_id=alice.id)
    assert len(balances) == 1
    assert balances[0]["name"] == "Bob"
    assert balances[0]["user_id"] is None
    assert balances[0]["is_frozen"] is True
    assert balances[0]["balance"] == Decimal("-50.00")


async def test_get_all_balances_omits_a_frozen_friend_whose_balance_is_now_zero(db_session):
    # Same scenario, but Bob settled up BEFORE deleting his account --
    # freeze_user_references doesn't touch Settlement rows (a real,
    # documented limitation), so this specifically tests that a
    # zero net balance (from splits alone, ignoring the pre-deletion
    # settlement) still correctly omits the entry once it nets to zero.
    alice, bob = await _make_users(db_session, "-frozen3")
    group = await create_group(db_session, name="Roommates", created_by=alice.id, member_ids=[bob.id])
    await create_shared_expense(
        db_session, group_id=group.id, paid_by=alice.id, description="Dinner",
        amount=Decimal("0.00"), expense_date="2026-07-08", participant_ids=[alice.id, bob.id],
    )
    await freeze_user_references(db_session, user_id=bob.id)

    balances = await get_all_balances(db_session, user_id=alice.id)
    assert balances == []


async def test_get_all_balances_includes_both_a_live_and_a_frozen_friend_together(db_session):
    alice, bob = await _make_users(db_session, "-frozen4")
    carol = User(email="carol-frozen4@example.com", hashed_password=hash_password("hunter2222"), display_name="Carol")
    db_session.add(carol)
    await db_session.commit()
    await db_session.refresh(carol)

    group = await create_group(db_session, name="Roommates", created_by=alice.id, member_ids=[bob.id, carol.id])
    await create_shared_expense(
        db_session, group_id=group.id, paid_by=alice.id, description="Dinner",
        amount=Decimal("100.00"), expense_date="2026-07-08", participant_ids=[alice.id, bob.id],
    )
    await create_shared_expense(
        db_session, group_id=group.id, paid_by=alice.id, description="Groceries",
        amount=Decimal("60.00"), expense_date="2026-07-08", participant_ids=[alice.id, carol.id],
    )
    await freeze_user_references(db_session, user_id=bob.id)  # only Bob's account is deleted

    balances = await get_all_balances(db_session, user_id=alice.id)
    names = {b["name"]: b["is_frozen"] for b in balances}
    assert names == {"Bob": True, "Carol": False}


# ---------- compute_group_debt_simplification ----------
#
# The real differentiator this exists for: pairwise balances (compute_balance)
# can't see a multi-person cycle. A owes B, B owes C, C owes A the same
# amount nets to ZERO total transfers needed once netted properly --
# but each pairwise balance looks non-zero in isolation.


async def test_simple_two_person_expense_needs_exactly_one_transfer(db_session):
    alice, bob = await _make_users(db_session, "-simplify1")
    group = await create_group(db_session, name="Trip", created_by=alice.id, member_ids=[bob.id])
    await create_shared_expense(
        db_session, group_id=group.id, paid_by=alice.id, description="Hotel",
        amount=Decimal("100.00"), expense_date="2026-07-08", participant_ids=[alice.id, bob.id],
    )
    transfers = await compute_group_debt_simplification(db_session, group_id=group.id)
    assert len(transfers) == 1
    assert transfers[0]["from_key"] == bob.id
    assert transfers[0]["to_key"] == alice.id
    assert transfers[0]["amount"] == Decimal("50.00")


async def test_a_perfect_three_person_cycle_simplifies_to_zero_transfers(db_session):
    # A pays for A+B, B pays for B+C, C pays for C+A, all equal splits
    # of the same amount -- a real, closed cycle where every dollar
    # someone is owed is exactly offset by a dollar they owe someone
    # else. Pairwise balances would show THREE non-zero relationships
    # (A<-B, B<-C, C<-A); simplification correctly finds that nothing
    # actually needs to change hands.
    alice, bob = await _make_users(db_session, "-cycle")
    carol = User(email="carol-cycle@example.com", hashed_password=hash_password("hunter2222"), display_name="Carol")
    db_session.add(carol)
    await db_session.commit()
    await db_session.refresh(carol)

    group = await create_group(db_session, name="Trio", created_by=alice.id, member_ids=[bob.id, carol.id])
    await create_shared_expense(
        db_session, group_id=group.id, paid_by=alice.id, description="Exp1",
        amount=Decimal("30.00"), expense_date="2026-07-08", participant_ids=[alice.id, bob.id],
    )
    await create_shared_expense(
        db_session, group_id=group.id, paid_by=bob.id, description="Exp2",
        amount=Decimal("30.00"), expense_date="2026-07-08", participant_ids=[bob.id, carol.id],
    )
    await create_shared_expense(
        db_session, group_id=group.id, paid_by=carol.id, description="Exp3",
        amount=Decimal("30.00"), expense_date="2026-07-08", participant_ids=[carol.id, alice.id],
    )

    # Confirm the pairwise view really does show 3 separate non-zero
    # balances first -- that's the whole point of the comparison.
    assert await compute_balance(db_session, user_a=bob.id, user_b=alice.id) == Decimal("15.00")
    assert await compute_balance(db_session, user_a=carol.id, user_b=bob.id) == Decimal("15.00")
    assert await compute_balance(db_session, user_a=alice.id, user_b=carol.id) == Decimal("15.00")

    transfers = await compute_group_debt_simplification(db_session, group_id=group.id)
    assert transfers == []


async def test_simplification_uses_fewer_transfers_than_the_number_of_expenses(db_session):
    # 4 people, star pattern: everyone owes Alice for something she
    # paid for the whole group each time. Should collapse to exactly
    # 3 transfers (one per debtor), not more.
    alice, bob = await _make_users(db_session, "-star")
    carol = User(email="carol-star@example.com", hashed_password=hash_password("hunter2222"), display_name="Carol")
    dave = User(email="dave-star@example.com", hashed_password=hash_password("hunter2222"), display_name="Dave")
    db_session.add(carol)
    db_session.add(dave)
    await db_session.commit()
    await db_session.refresh(carol)
    await db_session.refresh(dave)

    group = await create_group(db_session, name="Quad", created_by=alice.id, member_ids=[bob.id, carol.id, dave.id])
    for i in range(3):
        await create_shared_expense(
            db_session, group_id=group.id, paid_by=alice.id, description=f"Exp{i}",
            amount=Decimal("40.00"), expense_date="2026-07-08", participant_ids=[alice.id, bob.id, carol.id, dave.id],
        )
    transfers = await compute_group_debt_simplification(db_session, group_id=group.id)
    assert len(transfers) == 3
    assert {t["from_name"] for t in transfers} == {"Bob", "Carol", "Dave"}
    assert all(t["to_name"] == "Alice" for t in transfers)
    assert all(t["amount"] == Decimal("30.00") for t in transfers)  # 3 expenses x $10 share each


async def test_simplification_includes_a_frozen_participant(db_session):
    alice, bob = await _make_users(db_session, "-simplify-frozen")
    group = await create_group(db_session, name="Roommates", created_by=alice.id, member_ids=[bob.id])
    await create_shared_expense(
        db_session, group_id=group.id, paid_by=alice.id, description="Dinner",
        amount=Decimal("100.00"), expense_date="2026-07-08", participant_ids=[alice.id, bob.id],
    )
    await freeze_user_references(db_session, user_id=bob.id)

    transfers = await compute_group_debt_simplification(db_session, group_id=group.id)
    assert len(transfers) == 1
    assert transfers[0]["from_name"] == "Bob"
    assert transfers[0]["from_key"].startswith("frozen:")
    assert transfers[0]["to_key"] == alice.id


async def test_simplification_returns_empty_for_a_group_with_no_expenses(db_session):
    alice, bob = await _make_users(db_session, "-simplify-empty")
    group = await create_group(db_session, name="Empty", created_by=alice.id, member_ids=[bob.id])
    transfers = await compute_group_debt_simplification(db_session, group_id=group.id)
    assert transfers == []


# ---------- get_balance_breakdown ----------
#
# "Why do I owe this" -- the actual expenses and settlements behind
# a net balance, not just the total. Trust is the real product in
# shared money.


async def test_breakdown_includes_an_expense_line_for_each_direction(db_session):
    alice, bob = await _make_users(db_session, "-breakdown1")
    group = await create_group(db_session, name="Roommates", created_by=alice.id, member_ids=[bob.id])
    await create_shared_expense(
        db_session, group_id=group.id, paid_by=alice.id, description="Dinner",
        amount=Decimal("100.00"), expense_date="2026-07-08", participant_ids=[alice.id, bob.id],
    )
    await create_shared_expense(
        db_session, group_id=group.id, paid_by=bob.id, description="Groceries",
        amount=Decimal("40.00"), expense_date="2026-07-09", participant_ids=[alice.id, bob.id],
    )

    items = await get_balance_breakdown(db_session, user_id=alice.id, other_user_id=bob.id)
    assert len(items) == 2
    dinner = next(i for i in items if i["description"] == "Dinner")
    groceries = next(i for i in items if i["description"] == "Groceries")
    assert dinner["direction"] == "owed_to_you"  # alice paid, bob's share is owed to alice
    assert dinner["amount"] == Decimal("50.00")
    assert dinner["group_name"] == "Roommates"
    assert groceries["direction"] == "you_owe"  # bob paid, this is alice's share
    assert groceries["amount"] == Decimal("20.00")


async def test_breakdown_includes_settlement_lines(db_session):
    alice, bob = await _make_users(db_session, "-breakdown2")
    group = await create_group(db_session, name="Roommates", created_by=alice.id, member_ids=[bob.id])
    await create_shared_expense(
        db_session, group_id=group.id, paid_by=alice.id, description="Dinner",
        amount=Decimal("100.00"), expense_date="2026-07-08", participant_ids=[alice.id, bob.id],
    )
    await record_settlement(db_session, from_user_id=bob.id, to_user_id=alice.id, amount=Decimal("50.00"), settled_date="2026-07-10")

    items = await get_balance_breakdown(db_session, user_id=alice.id, other_user_id=bob.id)
    assert len(items) == 2
    settlement = next(i for i in items if i["type"] == "settlement")
    assert settlement["direction"] == "they_paid"  # bob paid alice, from alice's perspective
    assert settlement["amount"] == Decimal("50.00")
    assert settlement["group_name"] is None  # settlements aren't group-scoped


async def test_breakdown_is_sorted_oldest_first(db_session):
    alice, bob = await _make_users(db_session, "-breakdown3")
    group = await create_group(db_session, name="Trip", created_by=alice.id, member_ids=[bob.id])
    await create_shared_expense(
        db_session, group_id=group.id, paid_by=alice.id, description="Later expense",
        amount=Decimal("20.00"), expense_date="2026-07-15", participant_ids=[alice.id, bob.id],
    )
    await create_shared_expense(
        db_session, group_id=group.id, paid_by=alice.id, description="Earlier expense",
        amount=Decimal("20.00"), expense_date="2026-07-01", participant_ids=[alice.id, bob.id],
    )
    items = await get_balance_breakdown(db_session, user_id=alice.id, other_user_id=bob.id)
    assert [i["description"] for i in items] == ["Earlier expense", "Later expense"]


async def test_breakdown_is_empty_for_two_people_with_no_shared_history(db_session):
    alice, bob = await _make_users(db_session, "-breakdown4")
    items = await get_balance_breakdown(db_session, user_id=alice.id, other_user_id=bob.id)
    assert items == []


# ---------- get_settlements_received ----------
#
# The read side that lets a recipient's own app notice a settlement
# it doesn't know about yet -- Sanchay is local-first, so Bob
# recording "I paid Alice back" has no way to reach Alice's local
# ledger except by Alice's own app checking this and prompting her.


async def test_get_settlements_received_returns_a_settlement_paid_to_this_user(db_session):
    alice, bob = await _make_users(db_session, "-recv1")
    await record_settlement(db_session, from_user_id=bob.id, to_user_id=alice.id, amount=Decimal("50.00"), settled_date="2026-07-10")

    received = await get_settlements_received(db_session, user_id=alice.id)
    assert len(received) == 1
    assert received[0].from_user_id == bob.id
    assert received[0].to_user_id == alice.id
    assert received[0].amount == Decimal("50.00")


async def test_get_settlements_received_excludes_settlements_this_user_paid_out(db_session):
    alice, bob = await _make_users(db_session, "-recv2")
    await record_settlement(db_session, from_user_id=alice.id, to_user_id=bob.id, amount=Decimal("50.00"), settled_date="2026-07-10")

    received = await get_settlements_received(db_session, user_id=alice.id)
    assert received == []


async def test_get_settlements_received_is_sorted_oldest_first(db_session):
    alice, bob = await _make_users(db_session, "-recv3")
    await record_settlement(db_session, from_user_id=bob.id, to_user_id=alice.id, amount=Decimal("20.00"), settled_date="2026-07-15")
    await record_settlement(db_session, from_user_id=bob.id, to_user_id=alice.id, amount=Decimal("10.00"), settled_date="2026-07-01")

    received = await get_settlements_received(db_session, user_id=alice.id)
    assert [s.settled_date for s in received] == ["2026-07-01", "2026-07-15"]


async def test_get_settlements_received_is_empty_with_no_settlements_at_all(db_session):
    alice, _ = await _make_users(db_session, "-recv4")
    received = await get_settlements_received(db_session, user_id=alice.id)
    assert received == []
