from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.clerk_auth import get_clerk_user_id
from app.core.database import get_sanchay_app_db
from app.core.limiter import limiter
from app.models.savings_goal import SavingsGoal
from app.schemas.savings_goals import SavingsGoalCreateRequest, SavingsGoalOut, SavingsGoalUpdateRequest
from app.services import savings_goal_service

router = APIRouter(prefix="/savings-goals", tags=["savings-goals"])


async def _to_out(db: AsyncSession, clerk_user_id: str, goal: SavingsGoal) -> SavingsGoalOut:
    progress = await savings_goal_service.goal_progress(
        db, clerk_user_id=clerk_user_id, goal=goal, today=date.today()
    )
    return SavingsGoalOut(
        id=goal.id,
        name=goal.name,
        target_amount=float(goal.target_amount),
        target_date=goal.target_date.isoformat() if goal.target_date else None,
        account_id=goal.account_id,
        created_at=goal.created_at.isoformat(),
        updated_at=goal.updated_at.isoformat() if goal.updated_at else None,
        **progress,
    )


@router.post("", response_model=SavingsGoalOut, status_code=status.HTTP_201_CREATED)
@limiter.limit("60/minute")
async def create_savings_goal(
    request: Request,
    payload: SavingsGoalCreateRequest,
    clerk_user_id: str = Depends(get_clerk_user_id),
    db: AsyncSession = Depends(get_sanchay_app_db),
) -> SavingsGoalOut:
    goal = await savings_goal_service.create_savings_goal(
        db,
        clerk_user_id=clerk_user_id,
        name=payload.name,
        target_amount=payload.target_amount,
        target_date=payload.target_date,
        account_id=payload.account_id,
    )
    if goal is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Account not found")
    return await _to_out(db, clerk_user_id, goal)


@router.get("", response_model=list[SavingsGoalOut])
@limiter.limit("60/minute")
async def list_savings_goals(
    request: Request,
    clerk_user_id: str = Depends(get_clerk_user_id),
    db: AsyncSession = Depends(get_sanchay_app_db),
) -> list[SavingsGoalOut]:
    goals = await savings_goal_service.list_savings_goals(db, clerk_user_id=clerk_user_id)
    return [await _to_out(db, clerk_user_id, g) for g in goals]


@router.put("/{goal_id}", response_model=SavingsGoalOut)
@limiter.limit("60/minute")
async def update_savings_goal(
    request: Request,
    goal_id: str,
    payload: SavingsGoalUpdateRequest,
    clerk_user_id: str = Depends(get_clerk_user_id),
    db: AsyncSession = Depends(get_sanchay_app_db),
) -> SavingsGoalOut:
    goal = await savings_goal_service.update_savings_goal(
        db,
        clerk_user_id=clerk_user_id,
        goal_id=goal_id,
        name=payload.name,
        target_amount=payload.target_amount,
        target_date=payload.target_date,
        account_id=payload.account_id,
    )
    if goal is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND)
    return await _to_out(db, clerk_user_id, goal)


@router.delete("/{goal_id}", status_code=status.HTTP_204_NO_CONTENT)
@limiter.limit("60/minute")
async def delete_savings_goal(
    request: Request,
    goal_id: str,
    clerk_user_id: str = Depends(get_clerk_user_id),
    db: AsyncSession = Depends(get_sanchay_app_db),
) -> None:
    deleted = await savings_goal_service.delete_savings_goal(db, clerk_user_id=clerk_user_id, goal_id=goal_id)
    if not deleted:
        raise HTTPException(status.HTTP_404_NOT_FOUND)
