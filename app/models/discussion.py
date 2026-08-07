import uuid
from datetime import datetime, timezone

from sqlalchemy import DateTime, Index, Integer, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import SanchayAppBase


def _uuid() -> str:
    return str(uuid.uuid4())


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Discussion(SanchayAppBase):
    """
    Stores an already-analyzed discussion. The analysis itself
    (summary/decisions/actions/risks/suggestions/questions/sentiment)
    happens entirely client-side -- an on-device extractive-summarization
    and keyword-classification engine, ported from ledger-app's
    lib/discussionAnalysis.ts -- no transcript ever needs to reach an
    LLM or leave the browser to be analyzed. This model is just
    durable storage for the result, same reasoning ledger-app's own
    comment gives for why this needs no backend endpoint there (it
    rides along in a synced local blob); here it's a real row instead
    because Sanchay's storage model is server-backed, not local-first,
    but the computation stays exactly where ledger-app put it.
    """

    __tablename__ = "discussions"
    __table_args__ = (Index("ix_discussions_clerk_user_id", "clerk_user_id"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    clerk_user_id: Mapped[str] = mapped_column(String(255), nullable=False)
    title: Mapped[str] = mapped_column(String(256), nullable=False)
    transcript: Mapped[str] = mapped_column(Text, nullable=False)
    # Mirrors DiscussionAnalysis exactly (ledger-app's types.ts): summary,
    # decisions, actions, risks, suggestions, questions, sentiment.
    # Nullable because a discussion can be saved from a pasted transcript
    # that was never run through analysis (a real path in the recorder
    # UI, not a hypothetical).
    analysis: Mapped[dict | None] = mapped_column(JSON)
    duration_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), onupdate=_now)
