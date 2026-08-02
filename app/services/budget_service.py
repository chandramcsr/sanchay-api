from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.budget import Budget


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


async def list_budgets(db: AsyncSession, *, clerk_user_id: str) -> list[Budget]:
    result = await db.execute(select(Budget).where(Budget.clerk_user_id == clerk_user_id))
    return list(result.scalars().all())


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
