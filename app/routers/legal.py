from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.deps import get_current_user
from app.core.limiter import limiter
from app.models.legal_record import LegalRecord
from app.models.user import User
from app.schemas.legal import LegalRecordCreateRequest, LegalRecordOut, LegalRecordUpdateRequest
from app.services import legal_service

router = APIRouter(prefix="/legal", tags=["legal"])


def _to_out(record: LegalRecord) -> LegalRecordOut:
    return LegalRecordOut(
        id=record.id, record_type=record.record_type, title=record.title, status=record.status,
        key_date=record.key_date, amount=record.amount, counterparty=record.counterparty,
        document_location=record.document_location, notes=record.notes,
        created_at=record.created_at.isoformat(), updated_at=record.updated_at.isoformat(),
    )


@router.post("/records", response_model=LegalRecordOut, status_code=status.HTTP_201_CREATED)
@limiter.limit("60/minute")
async def create_record(
    request: Request,
    payload: LegalRecordCreateRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> LegalRecordOut:
    try:
        record = await legal_service.create_record(
            db, user_id=current_user.id, record_type=payload.record_type, title=payload.title,
            status=payload.status, key_date=payload.key_date, amount=payload.amount,
            counterparty=payload.counterparty, document_location=payload.document_location, notes=payload.notes,
        )
    except ValueError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(e))
    return _to_out(record)


@router.get("/records", response_model=list[LegalRecordOut])
@limiter.limit("60/minute")
async def list_records(
    request: Request,
    record_type: str | None = Query(default=None),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[LegalRecordOut]:
    records = await legal_service.list_records(db, user_id=current_user.id, record_type=record_type)
    return [_to_out(r) for r in records]


@router.put("/records/{record_id}", response_model=LegalRecordOut)
@limiter.limit("60/minute")
async def update_record(
    request: Request,
    record_id: str,
    payload: LegalRecordUpdateRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> LegalRecordOut:
    record = await legal_service.update_record(
        db, user_id=current_user.id, record_id=record_id, title=payload.title, status=payload.status,
        key_date=payload.key_date, amount=payload.amount, counterparty=payload.counterparty,
        document_location=payload.document_location, notes=payload.notes,
    )
    if record is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Record not found")
    return _to_out(record)


@router.delete("/records/{record_id}", status_code=status.HTTP_204_NO_CONTENT)
@limiter.limit("60/minute")
async def delete_record(
    request: Request, record_id: str, current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
) -> None:
    found = await legal_service.delete_record(db, user_id=current_user.id, record_id=record_id)
    if not found:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Record not found")
