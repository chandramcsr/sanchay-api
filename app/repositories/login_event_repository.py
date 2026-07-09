from sqlalchemy import delete as sa_delete
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.login_event import LoginEvent


def create(db: AsyncSession, *, user_id: str | None, email: str, success: bool, ip_address: str | None) -> LoginEvent:
    event = LoginEvent(user_id=user_id, email=email, success=success, ip_address=ip_address)
    db.add(event)
    return event


async def list_by_email(db: AsyncSession, email: str, limit: int) -> list[LoginEvent]:
    result = await db.execute(
        select(LoginEvent).where(LoginEvent.email == email).order_by(LoginEvent.created_at.desc()).limit(limit)
    )
    return list(result.scalars().all())


async def delete_by_user_id(db: AsyncSession, user_id: str) -> None:
    await db.execute(sa_delete(LoginEvent).where(LoginEvent.user_id == user_id))
