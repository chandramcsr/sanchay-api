from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.deps import get_current_user
from app.core.limiter import limiter
from app.models.user import User
from app.schemas.sync import SyncPullResponse, SyncPushRequest, SyncStatusResponse
from app.services import sync_service

router = APIRouter(prefix="/sync", tags=["sync"])


@router.get("/status", response_model=SyncStatusResponse)
async def sync_status(current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)) -> SyncStatusResponse:
    return await sync_service.get_status(db, current_user=current_user)


@router.get("/pull", response_model=SyncPullResponse)
async def sync_pull(current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)) -> SyncPullResponse:
    return await sync_service.pull(db, current_user=current_user)


@router.put("/push", response_model=SyncStatusResponse)
@limiter.limit("30/minute")
async def sync_push(
    request: Request,
    payload: SyncPushRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> SyncStatusResponse:
    return await sync_service.push(
        db,
        current_user=current_user,
        ciphertext=payload.ciphertext,
        encryption_meta=payload.encryption_meta,
        based_on_version=payload.based_on_version,
    )
