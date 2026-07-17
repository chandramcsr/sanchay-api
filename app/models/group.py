import uuid
from datetime import datetime, timezone

from sqlalchemy import DateTime, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


def _uuid() -> str:
    return str(uuid.uuid4())


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Group(Base):
    """
    A shared-expense group (roommates, a trip, etc.). Deliberately
    minimal — this whole module's only real dependency on the rest of
    the app is users.id (identity). No foreign keys into
    encrypted_ledgers or anything ledger-specific, on purpose: if this
    ever became its own service, moving it would mean copying these
    tables and their code, nothing else in sanchay-api would need to
    change.

    created_by is nullable — same freeze-not-cascade reasoning as
    GroupMember/SharedExpenseSplit/Settlement/etc: if the creator
    deletes their account, the group itself (and everyone still in it)
    has to survive, not get deleted or blocked from being deleted.
    created_by_name_snapshot preserves who created it once created_by
    is nulled, the same pattern used throughout this module.
    """

    __tablename__ = "groups"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    created_by: Mapped[str | None] = mapped_column(String(36), ForeignKey("users.id"), nullable=True)
    created_by_name_snapshot: Mapped[str | None] = mapped_column(String(200), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)
