from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.account import Account
from app.models.transaction import Transaction
from app.schemas.accounts import VALID_ACCOUNT_TYPES


async def create_account(
    db: AsyncSession, *, clerk_user_id: str, name: str, type: str, starting_balance: float, currency: str
) -> Account:
    if type not in VALID_ACCOUNT_TYPES:
        raise ValueError(f"type must be one of {sorted(VALID_ACCOUNT_TYPES)}")

    account = Account(
        clerk_user_id=clerk_user_id,
        name=name.strip(),
        type=type,
        starting_balance=starting_balance,
        currency=currency.upper(),
    )
    db.add(account)
    await db.commit()
    await db.refresh(account)
    return account


async def list_accounts_with_balance(db: AsyncSession, *, clerk_user_id: str) -> list[tuple[Account, float]]:
    """
    Returns (account, current_balance) pairs. current_balance is
    computed in SQL (starting_balance + SUM of every transaction on
    that account) rather than pulled into Python and summed there --
    avoids loading a user's entire transaction history into memory
    just to render account balances on a list screen.
    """
    balance_expr = Account.starting_balance + func.coalesce(func.sum(Transaction.amount), 0)
    query = (
        select(Account, balance_expr)
        .outerjoin(Transaction, Transaction.account_id == Account.id)
        .where(Account.clerk_user_id == clerk_user_id)
        .group_by(Account.id)
    )
    result = await db.execute(query)
    return [(row[0], float(row[1])) for row in result.all()]


async def get_account_balance(db: AsyncSession, *, clerk_user_id: str, account_id: str) -> float:
    balance_expr = Account.starting_balance + func.coalesce(func.sum(Transaction.amount), 0)
    query = (
        select(balance_expr)
        .select_from(Account)
        .outerjoin(Transaction, Transaction.account_id == Account.id)
        .where(Account.clerk_user_id == clerk_user_id, Account.id == account_id)
        .group_by(Account.id)
    )
    result = await db.execute(query)
    value = result.scalar_one_or_none()
    return float(value) if value is not None else 0.0


async def update_account(
    db: AsyncSession,
    *,
    clerk_user_id: str,
    account_id: str,
    name: str | None,
    type: str | None,
    starting_balance: float | None,
    currency: str | None,
) -> Account | None:
    """Returns None (not raise) when the account doesn't exist or isn't
    this user's -- the router turns that into a 404. Same
    not-found-not-403 reasoning used throughout the rest of this
    codebase's service layer (legal_service, health_service, etc.):
    ownership is checked in the WHERE clause of the lookup itself, not
    as a separate check after the fact, so there's no window where an
    account is fetched and only then found to belong to someone else."""
    if type is not None and type not in VALID_ACCOUNT_TYPES:
        raise ValueError(f"type must be one of {sorted(VALID_ACCOUNT_TYPES)}")

    result = await db.execute(
        select(Account).where(Account.id == account_id, Account.clerk_user_id == clerk_user_id)
    )
    account = result.scalar_one_or_none()
    if account is None:
        return None

    if name is not None:
        account.name = name.strip()
    if type is not None:
        account.type = type
    if starting_balance is not None:
        account.starting_balance = starting_balance
    if currency is not None:
        account.currency = currency.upper()

    await db.commit()
    await db.refresh(account)
    return account


async def delete_account(db: AsyncSession, *, clerk_user_id: str, account_id: str) -> bool:
    result = await db.execute(
        select(Account).where(Account.id == account_id, Account.clerk_user_id == clerk_user_id)
    )
    account = result.scalar_one_or_none()
    if account is None:
        return False

    await db.delete(account)
    # Cascades to transactions via the FK's ondelete="CASCADE" -- true
    # on Postgres (production) and in this test suite (conftest.py
    # explicitly enables SQLite's foreign_keys pragma, and
    # test_delete_account_cascades_to_transactions verifies this
    # directly). Only a plain local `uvicorn` run against dev.db
    # outside the test suite doesn't enforce it -- SQLite requires that
    # pragma per-connection and app/core/database.py's engine doesn't
    # set it (same pre-existing gap already present for shared_expense's
    # FK to groups, not something newly introduced here).
    await db.commit()
    return True
