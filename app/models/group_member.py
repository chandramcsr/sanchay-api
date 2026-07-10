import uuid
from datetime import datetime, timezone

from sqlalchemy import DateTime, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


def _uuid() -> str:
    return str(uuid.uuid4())


def _now() -> datetime:
    return datetime.now(timezone.utc)


class GroupMember(Base):
    """
    user_id is nullable with NO enforced foreign-key constraint — same
    freeze-not-cascade reasoning as SharedExpenseSplit. Group
    membership is itself part of the historical record ("Bob was in
    this group") that has to survive Bob deleting his account; a
    real FK constraint here would either block his account deletion
    outright or cascade-delete his membership row and erase the
    history, neither of which matches the freeze policy.
    """

    __tablename__ = "group_members"
    __table_args__ = (UniqueConstraint("group_id", "user_id", name="uq_group_member"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    group_id: Mapped[str] = mapped_column(String(36), ForeignKey("groups.id"), nullable=False, index=True)
    user_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    name_snapshot: Mapped[str] = mapped_column(String(200), nullable=False)
    joined_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)

