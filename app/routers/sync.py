from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import get_current_user
from app.core.limiter import limiter
from app.models.encrypted_ledger import EncryptedLedger
from app.models.user import User
from app.schemas.sync import SyncPullResponse, SyncPushRequest, SyncStatusResponse

router = APIRouter(prefix="/sync", tags=["sync"])


@router.get("/status", response_model=SyncStatusResponse)
def sync_status(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> SyncStatusResponse:
    """
    Cheap check before deciding whether to pull: does a backup exist
    for this account, and what version is it at? Lets the client
    compare against its own last-known version without downloading
    the (potentially large) ciphertext just to find out nothing changed.
    """
    ledger = db.get(EncryptedLedger, current_user.id)
    if ledger is None:
        return SyncStatusResponse(exists=False)
    return SyncStatusResponse(exists=True, version=ledger.version, updated_at=ledger.updated_at.isoformat())


@router.get("/pull", response_model=SyncPullResponse)
def sync_pull(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> SyncPullResponse:
    ledger = db.get(EncryptedLedger, current_user.id)
    if ledger is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="No backup exists yet for this account.")
    return SyncPullResponse(ciphertext=ledger.ciphertext, encryption_meta=ledger.encryption_meta, version=ledger.version)


@router.put("/push", response_model=SyncStatusResponse)
@limiter.limit("30/minute")
def sync_push(
    request: Request,
    payload: SyncPushRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> SyncStatusResponse:
    """
    Whole-blob replace with conflict detection — sync v1. The client
    declares which version it's replacing (based_on_version); if that
    doesn't match what's actually stored, the push is rejected with
    409 rather than silently overwriting changes the client hasn't
    seen yet. This is what prevents device A from clobbering device
    B's newer edits — the client is expected to pull the latest and
    reconcile (today: ask the user which copy to keep) before retrying.

    What this does NOT do: merge the two versions automatically. A
    real merge needs an operation log and field-level conflict
    resolution — meaningfully more work, deliberately not attempted
    here. This is "sync that can't silently lose data," not yet
    "sync that never asks you to choose."
    """
    ledger = db.get(EncryptedLedger, current_user.id)

    if ledger is None:
        if payload.based_on_version != 0:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                detail="No backup exists yet, but this push expected to replace an existing version.",
            )
        ledger = EncryptedLedger(
            user_id=current_user.id,
            ciphertext=payload.ciphertext,
            encryption_meta=payload.encryption_meta,
            version=1,
        )
        db.add(ledger)
    else:
        if payload.based_on_version != ledger.version:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                detail=f"Server has version {ledger.version}, which this push doesn't account for. Pull the latest first.",
            )
        ledger.ciphertext = payload.ciphertext
        ledger.encryption_meta = payload.encryption_meta
        ledger.version += 1

    db.commit()
    db.refresh(ledger)
    return SyncStatusResponse(exists=True, version=ledger.version, updated_at=ledger.updated_at.isoformat())
