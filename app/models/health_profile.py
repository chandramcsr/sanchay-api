import uuid
from datetime import datetime, timezone

from sqlalchemy import DateTime, Float, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


def _uuid() -> str:
    return str(uuid.uuid4())


def _now() -> datetime:
    return datetime.now(timezone.utc)


class HealthProfile(Base):
    """
    One row per user — baseline health info that changes rarely
    (height, age, gender) plus a free-text notes field for
    anything else worth having on hand (allergies, existing
    conditions) until there's a real reason to make that structured.

    Deliberately its own module, matching this codebase's established
    discipline for shared_expense_service: the ONLY foreign key out of
    this whole health domain is user_id, same as everywhere else. No
    reference into groups, shared_expenses, or encrypted_ledgers, and
    nothing in those other modules ever queries into health tables
    either — confirmed directly as the ground rule for this domain,
    specifically so a future split into its own service stays a real,
    low-cost option rather than a promise made and not kept.

    user_id is NOT nullable, unlike the shared-expenses freeze
    pattern -- deliberately different, not an inconsistency. Shared
    expenses freeze (null the reference, keep the row) because another
    person has a legitimate reason to still see "who owed what" after
    someone deletes their account. Nobody has an equivalent claim on
    someone's height or age -- there's no multi-party reason
    to retain it. So health data is fully, explicitly deleted when the
    account is (see auth_service.delete_account and
    health_service.delete_health_references), the same explicit-
    deletion pattern already used for LoginEvent/RefreshToken/etc.,
    not frozen.
    """

    __tablename__ = "health_profiles"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"), nullable=False, unique=True, index=True)
    height_cm: Mapped[float | None] = mapped_column(Float, nullable=True)
    age: Mapped[int | None] = mapped_column(nullable=True)
    gender: Mapped[str | None] = mapped_column(String(20), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now, nullable=False)
