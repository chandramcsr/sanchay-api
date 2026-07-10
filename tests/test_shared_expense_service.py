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
    create_group,
    create_shared_expense,
    edit_shared_expense,
    ensure_pending_invite,
    freeze_user_references,
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
