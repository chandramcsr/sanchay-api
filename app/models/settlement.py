import uuid
from datetime import datetime, timezone

from sqlalchemy import DateTime, ForeignKey, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


def _uuid() -> str:
    return str(uuid.uuid4())


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Settlement(Base):
    """
    A real payment between two people that reduces their outstanding
    balance — "Bob paid Alice $40 back." group_id is OPTIONAL: the
    balance itself is still always a running net between two people
    across everything they've shared (group_id doesn't change how
    compute_balance/compute_balance_with_frozen_friend net things),
    it's purely about where the payment shows up as an activity item.
    Left null, a settlement is a private, cross-group "we're square"
    between two people — never shown in any specific group's feed.
    Set to a real group, it shows there too, since sometimes a
    settlement genuinely IS "for this trip" and belongs in that
    group's history the same way an expense does.
    """

    __tablename__ = "settlements"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    group_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("groups.id"), nullable=True, index=True)
    from_user_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    from_email_ref: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    from_name_snapshot: Mapped[str] = mapped_column(String(200), nullable=False)
    to_user_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    to_email_ref: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    to_name_snapshot: Mapped[str] = mapped_column(String(200), nullable=False)
    amount: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)
    settled_date: Mapped[str] = mapped_column(String(10), nullable=False)  # YYYY-MM-DD
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)
