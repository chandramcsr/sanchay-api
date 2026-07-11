import uuid
from datetime import datetime, timezone

from sqlalchemy import JSON, DateTime, ForeignKey, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


def _uuid() -> str:
    return str(uuid.uuid4())


def _now() -> datetime:
    return datetime.now(timezone.utc)


class SharedRecurringRule(Base):
    """
    A schedule that materializes into real SharedExpense rows over
    time — "Rent, $2000, monthly, split 50/50" — mirroring exactly how
    the personal ledger's RecurringRule works (a rule stores the
    schedule; occurrences get materialized as real records, not
    computed on the fly at read time). The personal engine's own
    docstring explains why: materialization is a real, persisted
    catch-up, so if nobody opens the app/group for months, every due
    occurrence since last_materialized gets generated with its correct
    historical date — monthly totals and balances stay accurate
    retroactively, not just going forward.

    Unlike SharedExpense's splits (one row per participant, fully
    normalized — because a split is a real ledger fact with money
    that needs to individually settle, freeze, and reconnect), a
    recurring rule's participant list is a TEMPLATE, not itself a
    ledger entry — nothing here ever needs per-participant querying or
    settlement on its own, only "what should the next materialized
    expense's splits look like." A JSON column is the right tool for
    that: read/written as a whole, never joined against, exactly the
    profile JSON columns are actually good for. This is the first JSON
    column in this backend on purpose, not because JSON is generally
    preferred over normalized tables here (SharedExpenseSplit is
    normalized precisely because it needed to be).
    """

    __tablename__ = "shared_recurring_rules"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    group_id: Mapped[str] = mapped_column(String(36), ForeignKey("groups.id"), nullable=False, index=True)

    # Who set the rule up — same frozen-on-delete pattern as
    # SharedExpense.paid_by (nullable, no FK-enforced cascade; a
    # snapshot name survives the creator's account being deleted).
    created_by: Mapped[str | None] = mapped_column(String(36), nullable=True)
    created_by_name_snapshot: Mapped[str] = mapped_column(String(200), nullable=False)
    # Added after this table already had rows in production — server_default=""
    # backfills existing rules (an empty ref just means "never matches any
    # signup," the same as SharedExpense.paid_by_email_ref's own behavior
    # for a real, already-connected payer that never needs reconnecting).
    created_by_email_ref: Mapped[str] = mapped_column(String(64), nullable=False, server_default="", index=True)

    description: Mapped[str] = mapped_column(String(500), nullable=False)
    amount: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)
    category: Mapped[str] = mapped_column(String(50), nullable=False, server_default="Other")
    split_type: Mapped[str] = mapped_column(String(20), nullable=False, server_default="equal")

    # Template participant lists, same shapes create_shared_expense
    # already accepts: real user ids, and pending {email, name} dicts
    # for people not yet signed up. Materialization passes these
    # straight through to create_shared_expense unchanged.
    participant_ids: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    pending_participants: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    # Only meaningful when split_type != "equal" — same shape as
    # SharedExpenseCreateRequest.participant_values.
    participant_values: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)

    frequency: Mapped[str] = mapped_column(String(20), nullable=False)  # weekly | biweekly | monthly | quarterly | yearly
    start_date: Mapped[str] = mapped_column(String(10), nullable=False)  # YYYY-MM-DD, also the schedule's anchor
    end_date: Mapped[str | None] = mapped_column(String(10), nullable=True)  # inclusive; no occurrences after this
    last_materialized: Mapped[str | None] = mapped_column(String(10), nullable=True)  # YYYY-MM-DD of the most recent occurrence turned into a real SharedExpense

    active: Mapped[bool] = mapped_column(nullable=False, default=True)  # paused rules are kept, not deleted — same reasoning as everywhere else in this file: preserve history, don't destroy it

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now, nullable=False)
