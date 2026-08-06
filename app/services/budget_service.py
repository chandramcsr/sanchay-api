import calendar
from datetime import date

from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.budget import Budget
from app.models.transaction import Transaction


async def upsert_budget(db: AsyncSession, *, clerk_user_id: str, category: str, monthly_limit: float) -> Budget:
    """One budget per category per user by design (see the unique index
    on the model) -- setting a limit for a category the user already
    budgets for updates that row, not a silent duplicate sitting
    alongside it."""
    result = await db.execute(
        select(Budget).where(Budget.clerk_user_id == clerk_user_id, Budget.category == category)
    )
    existing = result.scalar_one_or_none()

    if existing is not None:
        existing.monthly_limit = monthly_limit
        await db.commit()
        await db.refresh(existing)
        return existing

    budget = Budget(clerk_user_id=clerk_user_id, category=category.strip(), monthly_limit=monthly_limit)
    db.add(budget)
    await db.commit()
    await db.refresh(budget)
    return budget


async def get_budget_spending(db: AsyncSession, *, clerk_user_id: str, category: str) -> float:
    today = date.today()
    month_start = today.replace(day=1)
    month_end = today.replace(day=calendar.monthrange(today.year, today.month)[1])

    query = select(func.coalesce(func.sum(func.abs(Transaction.amount)), 0)).where(
        Transaction.clerk_user_id == clerk_user_id,
        Transaction.category == category,
        Transaction.amount < 0,
        Transaction.date >= month_start,
        Transaction.date <= month_end,
    )
    result = await db.execute(query)
    value = result.scalar_one_or_none()
    return float(value) if value is not None else 0.0


async def list_budgets_with_spending(db: AsyncSession, *, clerk_user_id: str) -> list[tuple[Budget, float]]:
    """
    Returns (budget, spent_this_month) pairs. A budget with a limit but
    no visible spending against it isn't actually a budget -- it's a
    number sitting next to nothing to compare it to. spent_this_month
    is the sum of this calendar month's expense transactions (amount <
    0, matched by category) computed in SQL, same reasoning as account
    balances: don't pull a user's whole transaction history into
    Python just to total one month of one category.
    """
    today = date.today()
    month_start = today.replace(day=1)
    month_end = today.replace(day=calendar.monthrange(today.year, today.month)[1])

    spent_expr = func.coalesce(func.sum(func.abs(Transaction.amount)), 0)
    query = (
        select(Budget, spent_expr)
        .outerjoin(
            Transaction,
            and_(
                Transaction.clerk_user_id == Budget.clerk_user_id,
                Transaction.category == Budget.category,
                Transaction.amount < 0,
                Transaction.date >= month_start,
                Transaction.date <= month_end,
            ),
        )
        .where(Budget.clerk_user_id == clerk_user_id)
        .group_by(Budget.id)
    )
    result = await db.execute(query)
    return [(row[0], float(row[1])) for row in result.all()]


async def delete_budget(db: AsyncSession, *, clerk_user_id: str, budget_id: str) -> bool:
    result = await db.execute(
        select(Budget).where(Budget.id == budget_id, Budget.clerk_user_id == clerk_user_id)
    )
    budget = result.scalar_one_or_none()
    if budget is None:
        return False

    await db.delete(budget)
    await db.commit()
    return True
