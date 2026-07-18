from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.deps import get_current_user
from app.core.limiter import limiter
from app.models.user import User
from app.schemas.health import (
    HealthProfileOut,
    HealthProfileUpsertRequest,
    WeightEntryCreateRequest,
    WeightEntryOut,
)
from app.services import health_service

router = APIRouter(prefix="/health", tags=["health"])


@router.get("/profile", response_model=HealthProfileOut | None)
@limiter.limit("60/minute")
async def get_profile(
    request: Request, current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
) -> HealthProfileOut | None:
    profile = await health_service.get_profile(db, user_id=current_user.id)
    if profile is None:
        return None
    return HealthProfileOut(
        height_cm=profile.height_cm,
        date_of_birth=profile.date_of_birth,
        biological_sex=profile.biological_sex,
        notes=profile.notes,
        updated_at=profile.updated_at.isoformat(),
    )


@router.put("/profile", response_model=HealthProfileOut)
@limiter.limit("30/minute")
async def upsert_profile(
    request: Request,
    payload: HealthProfileUpsertRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> HealthProfileOut:
    try:
        profile = await health_service.upsert_profile(
            db, user_id=current_user.id,
            height_cm=payload.height_cm, date_of_birth=payload.date_of_birth,
            biological_sex=payload.biological_sex, notes=payload.notes,
        )
    except ValueError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(e))
    return HealthProfileOut(
        height_cm=profile.height_cm,
        date_of_birth=profile.date_of_birth,
        biological_sex=profile.biological_sex,
        notes=profile.notes,
        updated_at=profile.updated_at.isoformat(),
    )


@router.post("/weight", response_model=WeightEntryOut, status_code=status.HTTP_201_CREATED)
@limiter.limit("60/minute")
async def add_weight_entry(
    request: Request,
    payload: WeightEntryCreateRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> WeightEntryOut:
    entry = await health_service.add_weight_entry(
        db, user_id=current_user.id, weight_kg=payload.weight_kg, recorded_date=payload.recorded_date,
    )
    return WeightEntryOut(id=entry.id, weight_kg=entry.weight_kg, recorded_date=entry.recorded_date, created_at=entry.created_at.isoformat())


@router.get("/weight", response_model=list[WeightEntryOut])
@limiter.limit("60/minute")
async def list_weight_entries(
    request: Request, current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
) -> list[WeightEntryOut]:
    entries = await health_service.list_weight_entries(db, user_id=current_user.id)
    return [
        WeightEntryOut(id=e.id, weight_kg=e.weight_kg, recorded_date=e.recorded_date, created_at=e.created_at.isoformat())
        for e in entries
    ]


@router.delete("/weight/{entry_id}", status_code=status.HTTP_204_NO_CONTENT)
@limiter.limit("60/minute")
async def delete_weight_entry(
    request: Request, entry_id: str, current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
) -> None:
    found = await health_service.delete_weight_entry(db, user_id=current_user.id, entry_id=entry_id)
    if not found:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Entry not found")
