from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.legal_record import LegalRecord
from app.schemas.legal import VALID_RECORD_TYPES


async def create_record(
    db: AsyncSession, *, user_id: str, record_type: str, title: str, status: str | None,
    key_date: str | None, amount: float | None, counterparty: str | None,
    document_location: str | None, notes: str | None,
) -> LegalRecord:
    if record_type not in VALID_RECORD_TYPES:
        raise ValueError(f"record_type must be one of {sorted(VALID_RECORD_TYPES)}")

    record = LegalRecord(
        user_id=user_id, record_type=record_type, title=title.strip(), status=status,
        key_date=key_date, amount=amount, counterparty=counterparty,
        document_location=document_location, notes=notes.strip() if notes and notes.strip() else None,
    )
    db.add(record)
    await db.commit()
    await db.refresh(record)
    return record


async def list_records(db: AsyncSession, *, user_id: str, record_type: str | None = None) -> list[LegalRecord]:
    query = select(LegalRecord).where(LegalRecord.user_id == user_id)
    if record_type is not None:
        query = query.where(LegalRecord.record_type == record_type)
    result = await db.execute(query.order_by(LegalRecord.key_date.asc().nulls_last(), LegalRecord.created_at.desc()))
    return list(result.scalars().all())


async def update_record(
    db: AsyncSession, *, user_id: str, record_id: str, title: str, status: str | None,
    key_date: str | None, amount: float | None, counterparty: str | None,
    document_location: str | None, notes: str | None,
) -> LegalRecord | None:
    """Returns None (not True/raise) when the record doesn't exist or isn't this user's — the router turns that into a 404, same "not found, not 403" reasoning used throughout shared_expense_service and health_service."""
    result = await db.execute(select(LegalRecord).where(LegalRecord.id == record_id, LegalRecord.user_id == user_id))
    record = result.scalar_one_or_none()
    if record is None:
        return None

    record.title = title.strip()
    record.status = status
    record.key_date = key_date
    record.amount = amount
    record.counterparty = counterparty
    record.document_location = document_location
    record.notes = notes.strip() if notes and notes.strip() else None

    await db.commit()
    await db.refresh(record)
    return record


async def delete_record(db: AsyncSession, *, user_id: str, record_id: str) -> bool:
    """Same "not found, not 403" reasoning as update_record above."""
    result = await db.execute(select(LegalRecord).where(LegalRecord.id == record_id, LegalRecord.user_id == user_id))
    record = result.scalar_one_or_none()
    if record is None:
        return False
    await db.delete(record)
    await db.commit()
    return True


async def delete_legal_records(db: AsyncSession, *, user_id: str) -> None:
    """
    Called by auth_service.delete_account() BEFORE the user row is
    deleted — explicit deletion, not freezing (see LegalRecord's
    docstring). user_id is NOT nullable, so without this call the
    final DELETE FROM users would hit the same foreign-key violation
    already found and fixed for groups.created_by, Feedback.user_id,
    and every health table.
    """
    result = await db.execute(select(LegalRecord).where(LegalRecord.user_id == user_id))
    for row in result.scalars().all():
        await db.delete(row)
    await db.commit()
