from datetime import date as date_type

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.recurring_rule import RecurringRule
from app.models.transaction import Transaction
from app.services.account_service import owns_account
from app.services.recurring_date_math import due_occurrences


async def create_recurring_rule(
    db: AsyncSession,
    *,
    clerk_user_id: str,
    account_id: str,
    amount: float,
    description: str | None,
    category: str | None,
    frequency: str,
    start_date: str,
    end_date: str | None,
) -> RecurringRule | None:
    """Returns None if account_id doesn't belong to this user -- same
    not-found-not-403 pattern as transaction_service.create_transaction."""
    if not await owns_account(db, clerk_user_id=clerk_user_id, account_id=account_id):
        return None

    rule = RecurringRule(
        clerk_user_id=clerk_user_id,
        account_id=account_id,
        amount=amount,
        description=description.strip() if description and description.strip() else None,
        category=category.strip() if category and category.strip() else None,
        frequency=frequency,
        start_date=date_type.fromisoformat(start_date),
        end_date=date_type.fromisoformat(end_date) if end_date else None,
    )
    db.add(rule)
    await db.commit()
    await db.refresh(rule)
    return rule


async def list_recurring_rules(db: AsyncSession, *, clerk_user_id: str) -> list[RecurringRule]:
    result = await db.execute(select(RecurringRule).where(RecurringRule.clerk_user_id == clerk_user_id))
    return list(result.scalars().all())


async def update_recurring_rule(
    db: AsyncSession,
    *,
    clerk_user_id: str,
    rule_id: str,
    amount: float | None,
    description: str | None,
    category: str | None,
    frequency: str | None,
    end_date: str | None,
) -> RecurringRule | None:
    result = await db.execute(
        select(RecurringRule).where(RecurringRule.id == rule_id, RecurringRule.clerk_user_id == clerk_user_id)
    )
    rule = result.scalar_one_or_none()
    if rule is None:
        return None

    if amount is not None:
        rule.amount = amount
    if description is not None:
        rule.description = description.strip()
    if category is not None:
        rule.category = category.strip() or None
    if frequency is not None:
        rule.frequency = frequency
    if end_date is not None:
        rule.end_date = date_type.fromisoformat(end_date)

    await db.commit()
    await db.refresh(rule)
    return rule


async def delete_recurring_rule(db: AsyncSession, *, clerk_user_id: str, rule_id: str) -> bool:
    result = await db.execute(
        select(RecurringRule).where(RecurringRule.id == rule_id, RecurringRule.clerk_user_id == clerk_user_id)
    )
    rule = result.scalar_one_or_none()
    if rule is None:
        return False

    await db.delete(rule)
    await db.commit()
    return True


async def materialize_due_transactions(db: AsyncSession, *, clerk_user_id: str) -> int:
    """
    Catch-up materialization: for every one of this user's recurring
    rules, generate a real Transaction for every occurrence that's due
    (on or before today) and hasn't already been materialized. Reuses
    recurring_date_math.py -- the exact same date-math module already
    proven correct for shared recurring expenses, not a third
    reimplementation of anchor-preserving month arithmetic.

    Idempotent and safe to call as often as you like: due_occurrences
    only returns occurrences strictly after last_materialized, so
    calling this twice in a row with nothing new due generates nothing
    the second time. Called automatically whenever the frontend loads
    (see the accounts router), mirroring ledger-app's own
    "materialize whenever the app runs" philosophy -- a schedule that
    hasn't been checked in months still generates every occurrence it
    missed with the correct historical dates, not just the most recent
    one.

    Returns the count of transactions created, for callers that want
    to surface "N new transactions were added" rather than staying silent.
    """
    result = await db.execute(select(RecurringRule).where(RecurringRule.clerk_user_id == clerk_user_id))
    rules = list(result.scalars().all())

    today = date_type.today()
    created_count = 0

    for rule in rules:
        due_dates = due_occurrences(
            start_date=rule.start_date.isoformat(),
            frequency=rule.frequency,
            end_date=rule.end_date.isoformat() if rule.end_date else None,
            last_materialized=rule.last_materialized.isoformat() if rule.last_materialized else None,
            today=today.isoformat(),
        )
        if not due_dates:
            continue

        for occ in due_dates:
            db.add(
                Transaction(
                    clerk_user_id=rule.clerk_user_id,
                    account_id=rule.account_id,
                    amount=rule.amount,
                    description=rule.description,
                    category=rule.category,
                    date=date_type.fromisoformat(occ),
                )
            )
            created_count += 1

        # Latest occurrence generated, not `today` -- due_occurrences
        # already guarantees due_dates is chronologically sorted, so
        # the last element is the correct new watermark. Using `today`
        # instead would be wrong for a schedule whose next real
        # occurrence hasn't happened yet (e.g. monthly rent, checked
        # mid-month) -- it would still be correct today but silently
        # skip that occurrence once its actual due date arrives, since
        # last_materialized would already be past it.
        rule.last_materialized = date_type.fromisoformat(due_dates[-1])

    if created_count > 0:
        await db.commit()

    return created_count
