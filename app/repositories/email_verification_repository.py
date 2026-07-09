from sqlalchemy import delete as sa_delete
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.email_verification_token import EmailVerificationToken


def create(db: AsyncSession, *, user_id: str, token_hash: str) -> EmailVerificationToken:
    verification = EmailVerificationToken(user_id=user_id, token_hash=token_hash)
    db.add(verification)
    return verification


async def get_by_token_hash(db: AsyncSession, token_hash: str) -> EmailVerificationToken | None:
    result = await db.execute(select(EmailVerificationToken).where(EmailVerificationToken.token_hash == token_hash))
    return result.scalar_one_or_none()


async def count_by_user_id(db: AsyncSession, user_id: str) -> int:
    result = await db.execute(select(EmailVerificationToken).where(EmailVerificationToken.user_id == user_id))
    return len(result.scalars().all())


async def delete_by_user_id(db: AsyncSession, user_id: str) -> None:
    await db.execute(sa_delete(EmailVerificationToken).where(EmailVerificationToken.user_id == user_id))
