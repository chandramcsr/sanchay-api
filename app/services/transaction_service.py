from datetime import date as date_type
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.account import Account
from app.models.transaction import Transaction
from app.services.account_service import owns_account


async def create_transaction(
    db: AsyncSession,
    *,
    clerk_user_id: str,
    account_id: str,
    amount: float,
    description: str | None,
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
        description=description.strip() if description and description.strip() else None,
        category=category.strip() if category and category.strip() else None,
        date=date_type.fromisoformat(date),
    )
    db.add(transaction)
    await db.commit()
    await db.refresh(transaction)
    return transaction


async def create_transfer(
    db: AsyncSession,
    *,
    clerk_user_id: str,
    from_account_id: str,
    to_account_id: str,
    amount: float,
    date: str,
    note: str | None,
) -> tuple[Transaction, Transaction] | None:
    """
    Matches ledger-app's createTransfer exactly: two linked
    transactions sharing a transfer_group_id, an expense leg on the
    source account and an income leg on the destination, both real
    money movement (a credit card payment IS a transfer from
    checking, not an expense against either account) -- see the
    Transaction model's own docstring for the full reasoning.

    Returns None if either account doesn't belong to this user, or if
    both account ids are the same (a transfer needs two distinct
    accounts) -- the router turns either into a 4xx.
    """
    if from_account_id == to_account_id:
        return None
    if not await owns_account(db, clerk_user_id=clerk_user_id, account_id=from_account_id):
        return None
    if not await owns_account(db, clerk_user_id=clerk_user_id, account_id=to_account_id):
        return None

    result = await db.execute(
        select(Account).where(Account.id.in_([from_account_id, to_account_id]))
    )
    accounts_by_id = {a.id: a for a in result.scalars().all()}
    from_name = accounts_by_id[from_account_id].name
    to_name = accounts_by_id[to_account_id].name

    clean_note = note.strip() if note and note.strip() else None
    parsed_date = date_type.fromisoformat(date)
    group_id = str(uuid.uuid4())

    from_leg = Transaction(
        clerk_user_id=clerk_user_id,
        account_id=from_account_id,
        amount=-abs(amount),
        description=clean_note or f"To {to_name}",
        category="Transfer",
        date=parsed_date,
        transfer_group_id=group_id,
    )
    to_leg = Transaction(
        clerk_user_id=clerk_user_id,
        account_id=to_account_id,
        amount=abs(amount),
        description=clean_note or f"From {from_name}",
        category="Transfer",
        date=parsed_date,
        transfer_group_id=group_id,
    )
    db.add(from_leg)
    db.add(to_leg)
    await db.commit()
    await db.refresh(from_leg)
    await db.refresh(to_leg)
    return from_leg, to_leg


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

    if transaction.transfer_group_id is not None:
        # Delete both legs together -- a deliberate improvement over
        # ledger-app's own behavior, which leaves the other leg
        # orphaned (still tagged as a transfer, but with no matching
        # pair) when only one side is deleted. A half-deleted transfer
        # is a confusing, inconsistent state no user would actually
        # want; deleting the whole pair is what "delete this transfer"
        # should mean.
        pair_result = await db.execute(
            select(Transaction).where(
                Transaction.transfer_group_id == transaction.transfer_group_id,
                Transaction.clerk_user_id == clerk_user_id,
            )
        )
        for leg in pair_result.scalars().all():
            await db.delete(leg)
    else:
        await db.delete(transaction)

    await db.commit()
    return True
