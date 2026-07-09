"""
Sync business logic — the version-conflict detection that's the whole
point of this feature lives here, not in the router.
"""

from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user import User
from app.repositories import encrypted_ledger_repository
from app.schemas.sync import SyncPullResponse, SyncStatusResponse


async def get_status(db: AsyncSession, *, current_user: User) -> SyncStatusResponse:
    ledger = await encrypted_ledger_repository.get_by_user_id(db, current_user.id)
    if ledger is None:
        return SyncStatusResponse(exists=False)
    return SyncStatusResponse(exists=True, version=ledger.version, updated_at=ledger.updated_at.isoformat())


async def pull(db: AsyncSession, *, current_user: User) -> SyncPullResponse:
    ledger = await encrypted_ledger_repository.get_by_user_id(db, current_user.id)
    if ledger is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="No backup exists yet for this account.")
    return SyncPullResponse(ciphertext=ledger.ciphertext, encryption_meta=ledger.encryption_meta, version=ledger.version)


async def push(
    db: AsyncSession, *, current_user: User, ciphertext: str, encryption_meta: str, based_on_version: int
) -> SyncStatusResponse:
    """
    Whole-blob replace with conflict detection — sync v1. A stale push
    (based_on_version doesn't match what's actually stored) is
    rejected with 409 rather than silently overwriting changes the
    client hasn't seen yet. This is what prevents device A from
    clobbering device B's newer edits.
    """
    ledger = await encrypted_ledger_repository.get_by_user_id(db, current_user.id)

    if ledger is None:
        if based_on_version != 0:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                detail="No backup exists yet, but this push expected to replace an existing version.",
            )
        ledger = encrypted_ledger_repository.create(
            db, user_id=current_user.id, ciphertext=ciphertext, encryption_meta=encryption_meta
        )
    else:
        if based_on_version != ledger.version:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                detail=f"Server has version {ledger.version}, which this push doesn't account for. Pull the latest first.",
            )
        ledger = encrypted_ledger_repository.update(ledger, ciphertext=ciphertext, encryption_meta=encryption_meta)

    await db.commit()
    await db.refresh(ledger)
    return SyncStatusResponse(exists=True, version=ledger.version, updated_at=ledger.updated_at.isoformat())
