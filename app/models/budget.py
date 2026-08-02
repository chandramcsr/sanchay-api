import uuid
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import DateTime, Index, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import SanchayAppBase


def _uuid() -> str:
    return str(uuid.uuid4())


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Budget(SanchayAppBase):
    __tablename__ = "budgets"
    __table_args__ = (
        Index("ix_budgets_clerk_user_id", "clerk_user_id"),
        # One budget per category per user by design — setting a new
        # limit for a category the user already budgets for should
        # update that row (see the router's upsert), not create a
        # silent duplicate sitting alongside it. Enforced here, not just
        # assumed at the application layer, so a bug in the router
        # can't quietly leave two limits for the same category.
        Index("ux_budgets_clerk_user_id_category", "clerk_user_id", "category", unique=True),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    clerk_user_id: Mapped[str] = mapped_column(String(255), nullable=False)
    category: Mapped[str] = mapped_column(String(128), nullable=False)
    monthly_limit: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), onupdate=_now)
