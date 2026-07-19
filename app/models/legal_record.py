import uuid
from datetime import datetime, timezone

from sqlalchemy import DateTime, Float, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


def _uuid() -> str:
    return str(uuid.uuid4())


def _now() -> datetime:
    return datetime.now(timezone.utc)


class LegalRecord(Base):
    """
    A single personal legal/financial record the user is tracking —
    covers all seven items from BACKLOG.md's "Legal — Personal
    Records" section (will, nomination, contract, financial_dispute,
    insurance_claim, property_document, compliance_deadline) as ONE
    table with a record_type discriminator, not seven near-duplicate
    tables. The seven types share enough shape (a thing being
    tracked, with a date/party/amount/location/note) that this beats
    the alternative for a first version; can split into type-specific
    tables later if the generic shape proves limiting for a
    particular type.

    Deliberately pure record-keeping, matching the Legal tab's
    educational content's own ground rule: this table stores exactly
    what the user tells it and nothing the app derives, evaluates, or
    advises on. There is no field here for "case strength," "is this
    valid," or anything resembling a judgment — title, status,
    key_date, amount, counterparty, document_location, and notes are
    all just facts the user entered, verbatim.

    Own module, same isolation discipline as Health: the only foreign
    key out of this table is user_id, nothing reaches into
    shared_expenses/groups/encrypted_ledgers, and nothing there
    reaches in.

    user_id is NOT nullable — explicit deletion on account removal
    (see legal_service.delete_legal_records), not freezing. Same
    reasoning as HealthProfile: nobody else has a legitimate claim to
    see where someone's will is stored after they delete their
    account, unlike a shared expense's other participant.
    """

    __tablename__ = "legal_records"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"), nullable=False, index=True)
    record_type: Mapped[str] = mapped_column(String(30), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    status: Mapped[str | None] = mapped_column(String(30), nullable=True)
    key_date: Mapped[str | None] = mapped_column(String(10), nullable=True)  # YYYY-MM-DD
    amount: Mapped[float | None] = mapped_column(Float, nullable=True)
    counterparty: Mapped[str | None] = mapped_column(String(200), nullable=True)
    document_location: Mapped[str | None] = mapped_column(String(300), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now, nullable=False)
