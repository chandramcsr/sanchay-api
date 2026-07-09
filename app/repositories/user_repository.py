"""
Pure data access for User — no business logic, no commits. Committing
is a transaction-boundary decision that belongs to whichever service
is orchestrating a unit of work (which might touch several
repositories before deciding it's safe to commit once), not to the
repository itself.
"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user import User


async def get_by_email(db: AsyncSession, email: str) -> User | None:
    result = await db.execute(select(User).where(User.email == email))
    return result.scalar_one_or_none()


async def get_by_id(db: AsyncSession, user_id: str) -> User | None:
    return await db.get(User, user_id)


def create(db: AsyncSession, *, email: str, hashed_password: str, display_name: str) -> User:
    user = User(email=email, hashed_password=hashed_password, display_name=display_name)
    db.add(user)
    return user


async def delete(db: AsyncSession, user: User) -> None:
    await db.delete(user)
