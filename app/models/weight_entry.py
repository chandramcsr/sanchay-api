import uuid
from datetime import datetime, timezone

from sqlalchemy import DateTime, Float, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


def _uuid() -> str:
    return str(uuid.uuid4())


def _now() -> datetime:
    return datetime.now(timezone.utc)


class WeightEntry(Base):
    """
    A single weigh-in — many rows per user over time, the trend line
    this is really for. Same module boundary and same explicit-
    deletion-not-freeze reasoning as HealthProfile (see its docstring);
    the only difference here is there being many rows per person
    instead of one.

    Stored in metric (kg) as the one canonical unit, same reasoning
    as storing money in a fixed currency-agnostic form -- unit
    conversion for display (kg vs lb) is a presentation concern, not
    a storage one, and belongs in the client.
    """

    __tablename__ = "weight_entries"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"), nullable=False, index=True)
    weight_kg: Mapped[float] = mapped_column(Float, nullable=False)
    recorded_date: Mapped[str] = mapped_column(String(10), nullable=False, index=True)  # YYYY-MM-DD
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)
