from datetime import date as date_type
from typing import Callable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.transaction import Transaction
from app.services.account_service import owns_account
from app.services.embedding_service import embed_document, transaction_to_text


async def create_transaction(
    db: AsyncSession,
    *,
    clerk_user_id: str,
    account_id: str,
    amount: float,
    description: str | None,
    category: str | None,
    date: str,
    embed_fn: Callable[[str], list[float]] = embed_document,
) -> Transaction | None:
    """Returns None if account_id doesn't belong to this user -- the
    router turns that into a 404, same as everywhere else.

    embed_fn is injected (not imported and called directly) so tests
    can pass a stub and never need the real embedding model loaded --
    same pattern as rag_service.answer_question.
    """
    if not await owns_account(db, clerk_user_id=clerk_user_id, account_id=account_id):
        return None

    clean_description = description.strip() if description and description.strip() else None
    clean_category = category.strip() if category and category.strip() else None

    transaction = Transaction(
        clerk_user_id=clerk_user_id,
        account_id=account_id,
        amount=amount,
        description=clean_description,
        category=clean_category,
        date=date_type.fromisoformat(date),
        embedding=embed_fn(
            transaction_to_text(description=clean_description, amount=amount, category=clean_category, date=date)
        ),
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
    embed_fn: Callable[[str], list[float]] = embed_document,
) -> Transaction | None:
    result = await db.execute(
        select(Transaction).where(
            Transaction.id == transaction_id, Transaction.clerk_user_id == clerk_user_id
        )
    )
    transaction = result.scalar_one_or_none()
    if transaction is None:
        return None

    changed = amount is not None or description is not None or category is not None or date is not None

    if amount is not None:
        transaction.amount = amount
    if description is not None:
        transaction.description = description.strip()
    if category is not None:
        transaction.category = category.strip() or None
    if date is not None:
        transaction.date = date_type.fromisoformat(date)

    if changed:
        # Re-render from the transaction's current (post-update) state,
        # not just the fields that happened to change -- the embedded
        # sentence describes the whole transaction, so a stale field
        # left out of this update still needs to appear correctly.
        transaction.embedding = embed_fn(
            transaction_to_text(
                description=transaction.description,
                amount=float(transaction.amount),
                category=transaction.category,
                date=transaction.date.isoformat(),
            )
        )

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
