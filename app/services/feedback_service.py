from sqlalchemy.ext.asyncio import AsyncSession

from app.models.feedback import Feedback
from app.models.user import User

VALID_CATEGORIES = {"bug", "idea", "general"}


async def submit_feedback(
    db: AsyncSession, *, user: User, category: str, message: str, app_version: str | None
) -> Feedback:
    """
    Deliberately simple — no admin API reads this yet (queried
    directly against the database for now, per direct request), so
    there's nothing here beyond validating and inserting.
    """
    if category not in VALID_CATEGORIES:
        raise ValueError(f"category must be one of {sorted(VALID_CATEGORIES)}")

    feedback = Feedback(
        user_id=user.id,
        email_snapshot=user.email,
        category=category,
        message=message.strip(),
        app_version=app_version,
    )
    db.add(feedback)
    await db.commit()
    await db.refresh(feedback)
    return feedback
