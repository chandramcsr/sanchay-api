import uuid
from datetime import datetime, timezone

from sqlalchemy import DateTime, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


def _uuid() -> str:
    return str(uuid.uuid4())


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Feedback(Base):
    """
    In-app feedback — "Send Feedback" in Settings. Deliberately simple:
    no admin UI reads this yet (queried directly against the database
    for now), so the schema favors making a direct SQL query easy to
    read over building out relations/joins that only an API layer
    would need.

    user_id kept nullable with no cascade-delete, same freeze-not-
    destroy reasoning as everywhere else in this file's neighborhood
    (login_events, etc.) — if someone deletes their account later, the
    feedback they left is still worth having; email_snapshot preserves
    who said it without needing a live join back to a (possibly now
    gone) user row.

    app_version and submitted_at are auto-captured, not typed by the
    user — the whole point is a report that's actually actionable
    without a follow-up round-trip asking "which version were you on."
    """

    __tablename__ = "feedback"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("users.id"), nullable=True, index=True)
    email_snapshot: Mapped[str] = mapped_column(String(255), nullable=False)
    category: Mapped[str] = mapped_column(String(20), nullable=False)  # "bug" | "idea" | "general"
    message: Mapped[str] = mapped_column(Text, nullable=False)
    app_version: Mapped[str | None] = mapped_column(String(20), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False, index=True)
