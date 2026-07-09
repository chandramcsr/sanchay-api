from datetime import datetime, timezone

from sqlalchemy import delete as sa_delete
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.refresh_token import RefreshToken


def create(db: AsyncSession, *, user_id: str, token_hash: str) -> RefreshToken:
    token = RefreshToken(user_id=user_id, token_hash=token_hash)
    db.add(token)
    return token


async def get_by_token_hash(db: AsyncSession, token_hash: str) -> RefreshToken | None:
    result = await db.execute(select(RefreshToken).where(RefreshToken.token_hash == token_hash))
    return result.scalar_one_or_none()


def revoke(token: RefreshToken) -> None:
    token.revoked_at = datetime.now(timezone.utc)


async def delete_by_user_id(db: AsyncSession, user_id: str) -> None:
    await db.execute(sa_delete(RefreshToken).where(RefreshToken.user_id == user_id))
