from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.clerk_auth import get_clerk_user_id
from app.core.database import get_sanchay_app_db
from app.core.limiter import limiter
from app.models.transaction import Transaction
from app.schemas.transactions import (
    TransactionCreateRequest,
    TransactionOut,
    TransactionUpdateRequest,
    TransferCreateRequest,
    TransferOut,
)
from app.services import transaction_service

router = APIRouter(prefix="/transactions", tags=["transactions"])


def _to_out(t: Transaction) -> TransactionOut:
    return TransactionOut(
        id=t.id,
        account_id=t.account_id,
        amount=float(t.amount),
        description=t.description,
        category=t.category,
        date=t.date.isoformat(),
        transfer_group_id=t.transfer_group_id,
        created_at=t.created_at.isoformat(),
        updated_at=t.updated_at.isoformat() if t.updated_at else None,
    )


@router.post("", response_model=TransactionOut, status_code=status.HTTP_201_CREATED)
@limiter.limit("60/minute")
async def create_transaction(
    request: Request,
    payload: TransactionCreateRequest,
    clerk_user_id: str = Depends(get_clerk_user_id),
    db: AsyncSession = Depends(get_sanchay_app_db),
) -> TransactionOut:
    transaction = await transaction_service.create_transaction(
        db,
        clerk_user_id=clerk_user_id,
        account_id=payload.account_id,
        amount=payload.amount,
        description=payload.description,
        category=payload.category,
        date=payload.date,
    )
    if transaction is None:
        # account_id doesn't belong to this user -- 404, not 403 (see
        # account_service.owns_account), same not-found-not-403
        # reasoning used throughout this codebase's service layer.
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Account not found")
    return _to_out(transaction)


@router.post("/transfer", response_model=TransferOut, status_code=status.HTTP_201_CREATED)
@limiter.limit("60/minute")
async def create_transfer(
    request: Request,
    payload: TransferCreateRequest,
    clerk_user_id: str = Depends(get_clerk_user_id),
    db: AsyncSession = Depends(get_sanchay_app_db),
) -> TransferOut:
    result = await transaction_service.create_transfer(
        db,
        clerk_user_id=clerk_user_id,
        from_account_id=payload.from_account_id,
        to_account_id=payload.to_account_id,
        amount=payload.amount,
        date=payload.date,
        note=payload.note,
    )
    if result is None:
        # Either account doesn't belong to this user, or both ids are
        # the same -- 404 either way (see owns_account's not-found-
        # not-403 reasoning) rather than distinguishing which failure
        # in the response, same as everywhere else account ownership
        # is checked.
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Account not found, or from/to are the same account")
    from_leg, to_leg = result
    return TransferOut(from_transaction=_to_out(from_leg), to_transaction=_to_out(to_leg))


@router.get("", response_model=list[TransactionOut])
@limiter.limit("60/minute")
async def list_transactions(
    request: Request,
    account_id: str | None = Query(default=None),
    start_date: str | None = Query(default=None),
    end_date: str | None = Query(default=None),
    limit: int = Query(default=100, gt=0, le=500),
    clerk_user_id: str = Depends(get_clerk_user_id),
    db: AsyncSession = Depends(get_sanchay_app_db),
) -> list[TransactionOut]:
    transactions = await transaction_service.list_transactions(
        db,
        clerk_user_id=clerk_user_id,
        account_id=account_id,
        start_date=start_date,
        end_date=end_date,
        limit=limit,
    )
    return [_to_out(t) for t in transactions]


@router.put("/{transaction_id}", response_model=TransactionOut)
@limiter.limit("60/minute")
async def update_transaction(
    request: Request,
    transaction_id: str,
    payload: TransactionUpdateRequest,
    clerk_user_id: str = Depends(get_clerk_user_id),
    db: AsyncSession = Depends(get_sanchay_app_db),
) -> TransactionOut:
    transaction = await transaction_service.update_transaction(
        db,
        clerk_user_id=clerk_user_id,
        transaction_id=transaction_id,
        amount=payload.amount,
        description=payload.description,
        category=payload.category,
        date=payload.date,
    )
    if transaction is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND)
    return _to_out(transaction)


@router.delete("/{transaction_id}", status_code=status.HTTP_204_NO_CONTENT)
@limiter.limit("60/minute")
async def delete_transaction(
    request: Request,
    transaction_id: str,
    clerk_user_id: str = Depends(get_clerk_user_id),
    db: AsyncSession = Depends(get_sanchay_app_db),
) -> None:
    deleted = await transaction_service.delete_transaction(
        db, clerk_user_id=clerk_user_id, transaction_id=transaction_id
    )
    if not deleted:
        raise HTTPException(status.HTTP_404_NOT_FOUND)
