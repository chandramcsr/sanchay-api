from sqlalchemy import delete as sa_delete
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.password_reset_token import PasswordResetToken


def create(db: AsyncSession, *, user_id: str, token_hash: str) -> PasswordResetToken:
    reset = PasswordResetToken(user_id=user_id, token_hash=token_hash)
    db.add(reset)
    return reset


async def get_by_token_hash(db: AsyncSession, token_hash: str) -> PasswordResetToken | None:
    result = await db.execute(select(PasswordResetToken).where(PasswordResetToken.token_hash == token_hash))
    return result.scalar_one_or_none()


async def delete_by_user_id(db: AsyncSession, user_id: str) -> None:
    await db.execute(sa_delete(PasswordResetToken).where(PasswordResetToken.user_id == user_id))
