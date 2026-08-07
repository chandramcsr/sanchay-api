import uuid
from datetime import date as date_type
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import Date, DateTime, ForeignKey, Index, JSON, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import SanchayAppBase

# all-MiniLM-L6-v2 (sentence-transformers) produces 384-dim vectors --
# small and CPU-fast, the right tradeoff at this scale (one user, a
# few thousand transactions, not a search engine). If the embedding
# model ever changes, this dimension and every stored vector must
# change together -- see embedding_service.py.
EMBEDDING_DIM = 384


def _uuid() -> str:
    return str(uuid.uuid4())


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Transaction(SanchayAppBase):
    __tablename__ = "transactions"
    __table_args__ = (
        Index("ix_transactions_clerk_user_id", "clerk_user_id"),
        Index("ix_transactions_account_id", "account_id"),
        # Almost every real query is "this user's transactions in a date
        # range" (a statement, a month view, a budget calculation) — a
        # composite index matches that access pattern directly instead
        # of making Postgres intersect two separate single-column
        # indexes on every query.
        Index("ix_transactions_clerk_user_id_date", "clerk_user_id", "date"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    clerk_user_id: Mapped[str] = mapped_column(String(255), nullable=False)
    account_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False
    )
    # Signed: positive = income/credit, negative = expense/debit. One
    # column, one sign convention, rather than separate amount+type
    # columns that can drift out of sync with each other.
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    description: Mapped[str | None] = mapped_column(String(512))
    category: Mapped[str | None] = mapped_column(String(128))
    date: Mapped[date_type] = mapped_column(Date, nullable=False)
    # Embedding of the transaction rendered as a natural-language
    # sentence (see embedding_service.transaction_to_text), stored as
    # plain JSON rather than pgvector's native Vector type -- that
    # type is Postgres-only and has no SQLite equivalent, which would
    # break the test suite (in-memory SQLite). At this app's actual
    # scale (one user, a few thousand transactions) exact cosine
    # similarity computed in Python is fast enough that an ANN index
    # buys nothing -- that tradeoff flips well before a few hundred
    # thousand vectors, not at this size. Nullable because existing
    # rows need a separate backfill, not because a transaction can
    # meaningfully lack one long-term.
    embedding: Mapped[list[float] | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), onupdate=_now)
