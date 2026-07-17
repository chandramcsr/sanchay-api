from sqlalchemy import select
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


async def freeze_feedback_references(db: AsyncSession, *, user_id: str) -> None:
    """
    Called by auth_service.delete_account() BEFORE the user row is
    deleted — same reasoning and same bug class as
    shared_expense_service.freeze_user_references(), caught here
    proactively rather than waiting for a second report: user_id is
    nullable on Feedback (by design, per the model's own docstring --
    feedback is still worth having after someone deletes their
    account), but nothing actually nulled it before this fix, so the
    foreign key still blocked account deletion for anyone who'd ever
    submitted feedback. email_snapshot already preserves who said it,
    so there's no separate name-snapshot step needed here.
    """
    result = await db.execute(select(Feedback).where(Feedback.user_id == user_id))
    for row in result.scalars().all():
        row.user_id = None
    await db.commit()
