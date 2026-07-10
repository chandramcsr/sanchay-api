import uuid
from datetime import datetime, timezone

from sqlalchemy import DateTime, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


def _uuid() -> str:
    return str(uuid.uuid4())


def _now() -> datetime:
    return datetime.now(timezone.utc)


class PendingGroupInvite(Base):
    """
    Someone was added to a group by email, but doesn't have a Sanchay
    account yet. Unlike everywhere else in this module, this DOES
    store the raw email — necessary here specifically, since the
    whole point is emailing an invite to someone who has no user
    record (and therefore no email_ref could be derived) yet.
    Consumed and deleted the moment that email signs up
    (join_pending_invites, called from auth_service.signup(), the
    same integration point pattern as reconnect_by_email()).
    """

    __tablename__ = "pending_group_invites"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    group_id: Mapped[str] = mapped_column(String(36), ForeignKey("groups.id"), nullable=False, index=True)
    email: Mapped[str] = mapped_column(String(320), nullable=False, index=True)  # normalized lowercase
    name: Mapped[str] = mapped_column(String(200), nullable=False, server_default="")
    invited_by: Mapped[str | None] = mapped_column(String(36), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)
