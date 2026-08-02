from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.clerk_auth import get_clerk_user_id
from app.core.database import get_sanchay_app_db
from app.core.limiter import limiter
from app.models.budget import Budget
from app.schemas.budgets import BudgetOut, BudgetUpsertRequest
from app.services import budget_service

router = APIRouter(prefix="/budgets", tags=["budgets"])


def _to_out(b: Budget) -> BudgetOut:
    return BudgetOut(
        id=b.id,
        category=b.category,
        monthly_limit=float(b.monthly_limit),
        created_at=b.created_at.isoformat(),
        updated_at=b.updated_at.isoformat() if b.updated_at else None,
    )


@router.put("", response_model=BudgetOut)
@limiter.limit("60/minute")
async def upsert_budget(
    request: Request,
    payload: BudgetUpsertRequest,
    clerk_user_id: str = Depends(get_clerk_user_id),
    db: AsyncSession = Depends(get_sanchay_app_db),
) -> BudgetOut:
    budget = await budget_service.upsert_budget(
        db, clerk_user_id=clerk_user_id, category=payload.category, monthly_limit=payload.monthly_limit
    )
    return _to_out(budget)


@router.get("", response_model=list[BudgetOut])
@limiter.limit("60/minute")
async def list_budgets(
    request: Request,
    clerk_user_id: str = Depends(get_clerk_user_id),
    db: AsyncSession = Depends(get_sanchay_app_db),
) -> list[BudgetOut]:
    budgets = await budget_service.list_budgets(db, clerk_user_id=clerk_user_id)
    return [_to_out(b) for b in budgets]


@router.delete("/{budget_id}", status_code=status.HTTP_204_NO_CONTENT)
@limiter.limit("60/minute")
async def delete_budget(
    request: Request,
    budget_id: str,
    clerk_user_id: str = Depends(get_clerk_user_id),
    db: AsyncSession = Depends(get_sanchay_app_db),
) -> None:
    deleted = await budget_service.delete_budget(db, clerk_user_id=clerk_user_id, budget_id=budget_id)
    if not deleted:
        raise HTTPException(status.HTTP_404_NOT_FOUND)
