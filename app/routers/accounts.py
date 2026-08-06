from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.clerk_auth import get_clerk_user_id
from app.core.database import get_sanchay_app_db
from app.core.limiter import limiter
from app.models.account import Account
from app.schemas.accounts import AccountCreateRequest, AccountOut, AccountUpdateRequest
from app.services import account_service, recurring_service

router = APIRouter(prefix="/accounts", tags=["accounts"])


def _to_out(account: Account, current_balance: float) -> AccountOut:
    return AccountOut(
        id=account.id,
        name=account.name,
        type=account.type,
        starting_balance=float(account.starting_balance),
        current_balance=current_balance,
        currency=account.currency,
        created_at=account.created_at.isoformat(),
        updated_at=account.updated_at.isoformat() if account.updated_at else None,
    )


@router.post("", response_model=AccountOut, status_code=status.HTTP_201_CREATED)
@limiter.limit("60/minute")
async def create_account(
    request: Request,
    payload: AccountCreateRequest,
    clerk_user_id: str = Depends(get_clerk_user_id),
    db: AsyncSession = Depends(get_sanchay_app_db),
) -> AccountOut:
    try:
        account = await account_service.create_account(
            db,
            clerk_user_id=clerk_user_id,
            name=payload.name,
            type=payload.type,
            starting_balance=payload.starting_balance,
            currency=payload.currency,
        )
    except ValueError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(e))
    # A brand-new account has no transactions yet -- current balance
    # equals starting balance, no query needed to know that.
    return _to_out(account, float(account.starting_balance))


@router.get("", response_model=list[AccountOut])
@limiter.limit("60/minute")
async def list_accounts(
    request: Request,
    clerk_user_id: str = Depends(get_clerk_user_id),
    db: AsyncSession = Depends(get_sanchay_app_db),
) -> list[AccountOut]:
    # Materialize due recurring transactions before computing balances
    # -- GET /accounts is the natural first real data call after
    # sign-in (the frontend lands on /accounts), so this is where a
    # schedule that's come due gets turned into a real transaction and
    # reflected in the balance the user is about to see, rather than
    # requiring a separate action or staying silently un-applied until
    # something else happens to trigger it. Idempotent and cheap when
    # nothing's due (see materialize_due_transactions's own docstring).
    await recurring_service.materialize_due_transactions(db, clerk_user_id=clerk_user_id)

    rows = await account_service.list_accounts_with_balance(db, clerk_user_id=clerk_user_id)
    return [_to_out(account, balance) for account, balance in rows]


@router.put("/{account_id}", response_model=AccountOut)
@limiter.limit("60/minute")
async def update_account(
    request: Request,
    account_id: str,
    payload: AccountUpdateRequest,
    clerk_user_id: str = Depends(get_clerk_user_id),
    db: AsyncSession = Depends(get_sanchay_app_db),
) -> AccountOut:
    try:
        account = await account_service.update_account(
            db,
            clerk_user_id=clerk_user_id,
            account_id=account_id,
            name=payload.name,
            type=payload.type,
            starting_balance=payload.starting_balance,
            currency=payload.currency,
        )
    except ValueError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(e))
    if account is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND)

    balance = await account_service.get_account_balance(db, clerk_user_id=clerk_user_id, account_id=account.id)
    return _to_out(account, balance)


@router.delete("/{account_id}", status_code=status.HTTP_204_NO_CONTENT)
@limiter.limit("60/minute")
async def delete_account(
    request: Request,
    account_id: str,
    clerk_user_id: str = Depends(get_clerk_user_id),
    db: AsyncSession = Depends(get_sanchay_app_db),
) -> None:
    deleted = await account_service.delete_account(db, clerk_user_id=clerk_user_id, account_id=account_id)
    if not deleted:
        raise HTTPException(status.HTTP_404_NOT_FOUND)
