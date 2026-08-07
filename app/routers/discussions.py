from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.clerk_auth import get_clerk_user_id
from app.core.database import get_sanchay_app_db
from app.core.limiter import limiter
from app.models.discussion import Discussion
from app.schemas.discussions import (
    DiscussionCreateRequest,
    DiscussionListItemOut,
    DiscussionOut,
    DiscussionUpdateRequest,
)
from app.services import discussion_service

router = APIRouter(prefix="/discussions", tags=["discussions"])


def _to_out(d: Discussion) -> DiscussionOut:
    return DiscussionOut(
        id=d.id,
        title=d.title,
        transcript=d.transcript,
        analysis=d.analysis,
        duration_seconds=d.duration_seconds,
        created_at=d.created_at.isoformat(),
        updated_at=d.updated_at.isoformat() if d.updated_at else None,
    )


def _to_list_item(d: Discussion) -> DiscussionListItemOut:
    return DiscussionListItemOut(
        id=d.id, title=d.title, duration_seconds=d.duration_seconds, created_at=d.created_at.isoformat()
    )


@router.post("", response_model=DiscussionOut, status_code=status.HTTP_201_CREATED)
@limiter.limit("30/minute")
async def create_discussion(
    request: Request,
    payload: DiscussionCreateRequest,
    clerk_user_id: str = Depends(get_clerk_user_id),
    db: AsyncSession = Depends(get_sanchay_app_db),
) -> DiscussionOut:
    discussion = await discussion_service.create_discussion(
        db,
        clerk_user_id=clerk_user_id,
        title=payload.title,
        transcript=payload.transcript,
        analysis=payload.analysis.model_dump() if payload.analysis else None,
        duration_seconds=payload.duration_seconds,
    )
    return _to_out(discussion)


@router.get("", response_model=list[DiscussionListItemOut])
@limiter.limit("60/minute")
async def list_discussions(
    request: Request,
    clerk_user_id: str = Depends(get_clerk_user_id),
    db: AsyncSession = Depends(get_sanchay_app_db),
) -> list[DiscussionListItemOut]:
    discussions = await discussion_service.list_discussions(db, clerk_user_id=clerk_user_id)
    return [_to_list_item(d) for d in discussions]


@router.get("/{discussion_id}", response_model=DiscussionOut)
@limiter.limit("60/minute")
async def get_discussion(
    request: Request,
    discussion_id: str,
    clerk_user_id: str = Depends(get_clerk_user_id),
    db: AsyncSession = Depends(get_sanchay_app_db),
) -> DiscussionOut:
    discussion = await discussion_service.get_discussion(db, clerk_user_id=clerk_user_id, discussion_id=discussion_id)
    if discussion is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND)
    return _to_out(discussion)


@router.put("/{discussion_id}", response_model=DiscussionOut)
@limiter.limit("60/minute")
async def rename_discussion(
    request: Request,
    discussion_id: str,
    payload: DiscussionUpdateRequest,
    clerk_user_id: str = Depends(get_clerk_user_id),
    db: AsyncSession = Depends(get_sanchay_app_db),
) -> DiscussionOut:
    discussion = await discussion_service.rename_discussion(
        db, clerk_user_id=clerk_user_id, discussion_id=discussion_id, title=payload.title
    )
    if discussion is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND)
    return _to_out(discussion)


@router.delete("/{discussion_id}", status_code=status.HTTP_204_NO_CONTENT)
@limiter.limit("60/minute")
async def delete_discussion(
    request: Request,
    discussion_id: str,
    clerk_user_id: str = Depends(get_clerk_user_id),
    db: AsyncSession = Depends(get_sanchay_app_db),
) -> None:
    deleted = await discussion_service.delete_discussion(db, clerk_user_id=clerk_user_id, discussion_id=discussion_id)
    if not deleted:
        raise HTTPException(status.HTTP_404_NOT_FOUND)
