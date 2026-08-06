from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.clerk_auth import get_clerk_user_id
from app.core.database import get_sanchay_app_db
from app.core.limiter import limiter
from app.models.recurring_rule import RecurringRule
from app.schemas.recurring_rules import (
    VALID_FREQUENCIES,
    RecurringRuleCreateRequest,
    RecurringRuleOut,
    RecurringRuleUpdateRequest,
)
from app.services import recurring_service

router = APIRouter(prefix="/recurring-rules", tags=["recurring-rules"])


def _to_out(r: RecurringRule) -> RecurringRuleOut:
    return RecurringRuleOut(
        id=r.id,
        account_id=r.account_id,
        amount=float(r.amount),
        description=r.description,
        category=r.category,
        frequency=r.frequency,
        start_date=r.start_date.isoformat(),
        end_date=r.end_date.isoformat() if r.end_date else None,
        last_materialized=r.last_materialized.isoformat() if r.last_materialized else None,
        created_at=r.created_at.isoformat(),
        updated_at=r.updated_at.isoformat() if r.updated_at else None,
    )


@router.post("", response_model=RecurringRuleOut, status_code=status.HTTP_201_CREATED)
@limiter.limit("60/minute")
async def create_recurring_rule(
    request: Request,
    payload: RecurringRuleCreateRequest,
    clerk_user_id: str = Depends(get_clerk_user_id),
    db: AsyncSession = Depends(get_sanchay_app_db),
) -> RecurringRuleOut:
    if payload.frequency not in VALID_FREQUENCIES:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=f"frequency must be one of {sorted(VALID_FREQUENCIES)}")

    rule = await recurring_service.create_recurring_rule(
        db,
        clerk_user_id=clerk_user_id,
        account_id=payload.account_id,
        amount=payload.amount,
        description=payload.description,
        category=payload.category,
        frequency=payload.frequency,
        start_date=payload.start_date,
        end_date=payload.end_date,
    )
    if rule is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Account not found")
    return _to_out(rule)


@router.get("", response_model=list[RecurringRuleOut])
@limiter.limit("60/minute")
async def list_recurring_rules(
    request: Request,
    clerk_user_id: str = Depends(get_clerk_user_id),
    db: AsyncSession = Depends(get_sanchay_app_db),
) -> list[RecurringRuleOut]:
    rules = await recurring_service.list_recurring_rules(db, clerk_user_id=clerk_user_id)
    return [_to_out(r) for r in rules]


@router.put("/{rule_id}", response_model=RecurringRuleOut)
@limiter.limit("60/minute")
async def update_recurring_rule(
    request: Request,
    rule_id: str,
    payload: RecurringRuleUpdateRequest,
    clerk_user_id: str = Depends(get_clerk_user_id),
    db: AsyncSession = Depends(get_sanchay_app_db),
) -> RecurringRuleOut:
    if payload.frequency is not None and payload.frequency not in VALID_FREQUENCIES:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=f"frequency must be one of {sorted(VALID_FREQUENCIES)}")

    rule = await recurring_service.update_recurring_rule(
        db,
        clerk_user_id=clerk_user_id,
        rule_id=rule_id,
        amount=payload.amount,
        description=payload.description,
        category=payload.category,
        frequency=payload.frequency,
        end_date=payload.end_date,
    )
    if rule is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND)
    return _to_out(rule)


@router.delete("/{rule_id}", status_code=status.HTTP_204_NO_CONTENT)
@limiter.limit("60/minute")
async def delete_recurring_rule(
    request: Request,
    rule_id: str,
    clerk_user_id: str = Depends(get_clerk_user_id),
    db: AsyncSession = Depends(get_sanchay_app_db),
) -> None:
    deleted = await recurring_service.delete_recurring_rule(db, clerk_user_id=clerk_user_id, rule_id=rule_id)
    if not deleted:
        raise HTTPException(status.HTTP_404_NOT_FOUND)
