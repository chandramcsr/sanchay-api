from datetime import date as date_type
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.savings_goal import SavingsGoal
from app.models.transaction import Transaction
from app.services.account_service import get_account_balance, owns_account


def _add_months_js_style(date_str: str, months: float) -> str:
    """
    Mirrors ledger-app's savingsGoals.ts addMonths() exactly -- a
    plain JS `Date.setMonth(getMonth() + months)` call, which OVERFLOWS
    into subsequent days when the target month doesn't have that day
    (Jan 31 + 1 month -> Mar 3, not clamped to Feb 28/29). This is
    deliberately different from recurring_date_math.py's
    _add_months_anchored, which clamps instead -- that function exists
    for a different purpose (preserving a recurring schedule's anchor
    day exactly), while this one is just porting JS's own arithmetic
    faithfully for a rough "around when will this be done" estimate,
    not a precise schedule.
    """
    d = date_type.fromisoformat(date_str)
    m = int(months)  # JS coerces a fractional month count toward zero in setMonth
    total = d.year * 12 + (d.month - 1) + m
    new_year = total // 12
    new_month = total % 12 + 1
    base = date_type(new_year, new_month, 1)
    return (base + timedelta(days=d.day - 1)).isoformat()


async def _recent_monthly_rate(db: AsyncSession, *, clerk_user_id: str, account_id: str, today: date_type) -> float:
    """
    Average net inflow to this account over the trailing 90 days, per
    month -- mirrors recentMonthlyRate() in ledger-app's
    savingsGoals.ts exactly. Transaction.amount here is already signed
    (positive = income, negative = expense), so summing it directly
    gives net inflow without needing ledger-app's `type === "income" ?
    amount : -amount` branch -- that branch exists there because its
    amount is unsigned with a separate type field; this app's data
    model already encodes direction in the sign.
    """
    cutoff = today - timedelta(days=90)
    query = select(Transaction.amount).where(
        Transaction.clerk_user_id == clerk_user_id,
        Transaction.account_id == account_id,
        Transaction.date >= cutoff,
        Transaction.date <= today,
    )
    result = await db.execute(query)
    net = sum(float(row[0]) for row in result.all())
    return net / 3  # 3 months of history -> average per month


async def goal_progress(db: AsyncSession, *, clerk_user_id: str, goal: SavingsGoal, today: date_type) -> dict:
    """
    A goal's progress is its linked account's current balance -- see
    the model docstring for why. Mirrors goalProgress() in
    ledger-app's savingsGoals.ts field-for-field.
    """
    current_amount = await get_account_balance(db, clerk_user_id=clerk_user_id, account_id=goal.account_id)
    target_amount = float(goal.target_amount)

    pct = min(100.0, max(0.0, (current_amount / target_amount) * 100)) if target_amount > 0 else 0.0
    remaining = max(0.0, target_amount - current_amount)

    monthly_rate = await _recent_monthly_rate(
        db, clerk_user_id=clerk_user_id, account_id=goal.account_id, today=today
    )

    projected_completion_date: str | None = None
    if remaining <= 0:
        projected_completion_date = today.isoformat()
    elif monthly_rate > 0:
        months_needed = remaining / monthly_rate
        projected_completion_date = _add_months_js_style(today.isoformat(), months_needed)

    on_track_for_target_date: bool | None = None
    if goal.target_date is not None:
        target_date_str = goal.target_date.isoformat()
        if remaining <= 0:
            on_track_for_target_date = True
        elif projected_completion_date is None:
            on_track_for_target_date = False
        else:
            on_track_for_target_date = projected_completion_date <= target_date_str

    return {
        "current_amount": current_amount,
        "pct": pct,
        "remaining": remaining,
        "monthly_contribution_rate": monthly_rate,
        "projected_completion_date": projected_completion_date,
        "on_track_for_target_date": on_track_for_target_date,
    }


async def create_savings_goal(
    db: AsyncSession,
    *,
    clerk_user_id: str,
    name: str,
    target_amount: float,
    target_date: str | None,
    account_id: str,
) -> SavingsGoal | None:
    if not await owns_account(db, clerk_user_id=clerk_user_id, account_id=account_id):
        return None

    goal = SavingsGoal(
        clerk_user_id=clerk_user_id,
        name=name.strip(),
        target_amount=target_amount,
        target_date=date_type.fromisoformat(target_date) if target_date else None,
        account_id=account_id,
    )
    db.add(goal)
    await db.commit()
    await db.refresh(goal)
    return goal


async def list_savings_goals(db: AsyncSession, *, clerk_user_id: str) -> list[SavingsGoal]:
    result = await db.execute(select(SavingsGoal).where(SavingsGoal.clerk_user_id == clerk_user_id))
    return list(result.scalars().all())


async def update_savings_goal(
    db: AsyncSession,
    *,
    clerk_user_id: str,
    goal_id: str,
    name: str | None,
    target_amount: float | None,
    target_date: str | None,
    account_id: str | None,
) -> SavingsGoal | None:
    result = await db.execute(
        select(SavingsGoal).where(SavingsGoal.id == goal_id, SavingsGoal.clerk_user_id == clerk_user_id)
    )
    goal = result.scalar_one_or_none()
    if goal is None:
        return None

    if account_id is not None:
        if not await owns_account(db, clerk_user_id=clerk_user_id, account_id=account_id):
            return None
        goal.account_id = account_id
    if name is not None:
        goal.name = name.strip()
    if target_amount is not None:
        goal.target_amount = target_amount
    if target_date is not None:
        goal.target_date = date_type.fromisoformat(target_date)

    await db.commit()
    await db.refresh(goal)
    return goal


async def delete_savings_goal(db: AsyncSession, *, clerk_user_id: str, goal_id: str) -> bool:
    result = await db.execute(
        select(SavingsGoal).where(SavingsGoal.id == goal_id, SavingsGoal.clerk_user_id == clerk_user_id)
    )
    goal = result.scalar_one_or_none()
    if goal is None:
        return False

    await db.delete(goal)
    await db.commit()
    return True
