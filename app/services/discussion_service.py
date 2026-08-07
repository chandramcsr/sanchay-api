from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.discussion import Discussion


async def create_discussion(
    db: AsyncSession,
    *,
    clerk_user_id: str,
    title: str,
    transcript: str,
    analysis: dict | None,
    duration_seconds: int,
) -> Discussion:
    discussion = Discussion(
        clerk_user_id=clerk_user_id,
        title=title.strip(),
        transcript=transcript,
        analysis=analysis,
        duration_seconds=duration_seconds,
    )
    db.add(discussion)
    await db.commit()
    await db.refresh(discussion)
    return discussion


async def list_discussions(db: AsyncSession, *, clerk_user_id: str) -> list[Discussion]:
    query = select(Discussion).where(Discussion.clerk_user_id == clerk_user_id).order_by(Discussion.created_at.desc())
    result = await db.execute(query)
    return list(result.scalars().all())


async def get_discussion(db: AsyncSession, *, clerk_user_id: str, discussion_id: str) -> Discussion | None:
    result = await db.execute(
        select(Discussion).where(Discussion.id == discussion_id, Discussion.clerk_user_id == clerk_user_id)
    )
    return result.scalar_one_or_none()


async def rename_discussion(
    db: AsyncSession, *, clerk_user_id: str, discussion_id: str, title: str
) -> Discussion | None:
    discussion = await get_discussion(db, clerk_user_id=clerk_user_id, discussion_id=discussion_id)
    if discussion is None:
        return None
    discussion.title = title.strip()
    await db.commit()
    await db.refresh(discussion)
    return discussion


async def delete_discussion(db: AsyncSession, *, clerk_user_id: str, discussion_id: str) -> bool:
    discussion = await get_discussion(db, clerk_user_id=clerk_user_id, discussion_id=discussion_id)
    if discussion is None:
        return False
    await db.delete(discussion)
    await db.commit()
    return True
