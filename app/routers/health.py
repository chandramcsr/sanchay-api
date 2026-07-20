from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.deps import get_current_user
from app.core.limiter import limiter
from app.models.user import User
from app.schemas.health import (
    BloodPressureEntryCreateRequest,
    BloodPressureEntryOut,
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
        age=profile.age,
        gender=profile.gender,
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
            height_cm=payload.height_cm, age=payload.age,
            gender=payload.gender, notes=payload.notes,
        )
    except ValueError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(e))
    return HealthProfileOut(
        height_cm=profile.height_cm,
        age=profile.age,
        gender=profile.gender,
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


@router.post("/blood-pressure", response_model=BloodPressureEntryOut, status_code=status.HTTP_201_CREATED)
@limiter.limit("60/minute")
async def add_blood_pressure_entry(
    request: Request,
    payload: BloodPressureEntryCreateRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> BloodPressureEntryOut:
    entry = await health_service.add_blood_pressure_entry(
        db, user_id=current_user.id, systolic=payload.systolic, diastolic=payload.diastolic,
        pulse=payload.pulse, recorded_date=payload.recorded_date,
    )
    return BloodPressureEntryOut(
        id=entry.id, systolic=entry.systolic, diastolic=entry.diastolic, pulse=entry.pulse,
        recorded_date=entry.recorded_date, created_at=entry.created_at.isoformat(),
    )


@router.get("/blood-pressure", response_model=list[BloodPressureEntryOut])
@limiter.limit("60/minute")
async def list_blood_pressure_entries(
    request: Request, current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
) -> list[BloodPressureEntryOut]:
    entries = await health_service.list_blood_pressure_entries(db, user_id=current_user.id)
    return [
        BloodPressureEntryOut(
            id=e.id, systolic=e.systolic, diastolic=e.diastolic, pulse=e.pulse,
            recorded_date=e.recorded_date, created_at=e.created_at.isoformat(),
        )
        for e in entries
    ]


@router.delete("/blood-pressure/{entry_id}", status_code=status.HTTP_204_NO_CONTENT)
@limiter.limit("60/minute")
async def delete_blood_pressure_entry(
    request: Request, entry_id: str, current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
) -> None:
    found = await health_service.delete_blood_pressure_entry(db, user_id=current_user.id, entry_id=entry_id)
    if not found:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Entry not found")
