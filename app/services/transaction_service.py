from datetime import date as date_type

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.transaction import Transaction
from app.services.account_service import owns_account


async def create_transaction(
    db: AsyncSession,
    *,
    clerk_user_id: str,
    account_id: str,
    amount: float,
    description: str,
    category: str | None,
    date: str,
) -> Transaction | None:
    """Returns None if account_id doesn't belong to this user -- the
    router turns that into a 404, same as everywhere else."""
    if not await owns_account(db, clerk_user_id=clerk_user_id, account_id=account_id):
        return None

    transaction = Transaction(
        clerk_user_id=clerk_user_id,
        account_id=account_id,
        amount=amount,
        description=description.strip(),
        category=category.strip() if category and category.strip() else None,
        date=date_type.fromisoformat(date),
    )
    db.add(transaction)
    await db.commit()
    await db.refresh(transaction)
    return transaction


async def list_transactions(
    db: AsyncSession,
    *,
    clerk_user_id: str,
    account_id: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    limit: int = 100,
) -> list[Transaction]:
    query = select(Transaction).where(Transaction.clerk_user_id == clerk_user_id)
    if account_id is not None:
        query = query.where(Transaction.account_id == account_id)
    if start_date is not None:
        query = query.where(Transaction.date >= date_type.fromisoformat(start_date))
    if end_date is not None:
        query = query.where(Transaction.date <= date_type.fromisoformat(end_date))

    result = await db.execute(query.order_by(Transaction.date.desc()).limit(limit))
    return list(result.scalars().all())


async def update_transaction(
    db: AsyncSession,
    *,
    clerk_user_id: str,
    transaction_id: str,
    amount: float | None,
    description: str | None,
    category: str | None,
    date: str | None,
) -> Transaction | None:
    result = await db.execute(
        select(Transaction).where(
            Transaction.id == transaction_id, Transaction.clerk_user_id == clerk_user_id
        )
    )
    transaction = result.scalar_one_or_none()
    if transaction is None:
        return None

    if amount is not None:
        transaction.amount = amount
    if description is not None:
        transaction.description = description.strip()
    if category is not None:
        transaction.category = category.strip() or None
    if date is not None:
        transaction.date = date_type.fromisoformat(date)

    await db.commit()
    await db.refresh(transaction)
    return transaction


async def delete_transaction(db: AsyncSession, *, clerk_user_id: str, transaction_id: str) -> bool:
    result = await db.execute(
        select(Transaction).where(
            Transaction.id == transaction_id, Transaction.clerk_user_id == clerk_user_id
        )
    )
    transaction = result.scalar_one_or_none()
    if transaction is None:
        return False

    await db.delete(transaction)
    await db.commit()
    return True
