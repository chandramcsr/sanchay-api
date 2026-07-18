import uuid
from datetime import datetime, timezone

from sqlalchemy import DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


def _uuid() -> str:
    return str(uuid.uuid4())


def _now() -> datetime:
    return datetime.now(timezone.utc)


class BloodPressureEntry(Base):
    """
    A single reading — many rows per user over time, same shape as
    WeightEntry (own module, explicit-deletion-not-freeze, see
    HealthProfile's docstring for why), except this is the first
    dual-value vital: systolic and diastolic together as one reading,
    not two separate metric rows. They're only ever meaningful as a
    pair (120 systolic alone says nothing without its diastolic), so
    splitting them into separate rows would let one half of a reading
    exist without the other, and would need a join to reconstruct
    what should just be one entry from the start. pulse is optional --
    plenty of BP monitors report it alongside the reading, but it's
    not always available depending on the device.

    Integer, not Float, unlike weight -- blood pressure is
    conventionally reported and read as whole mmHg (a monitor doesn't
    show "120.5"), so this matches how the data actually looks rather
    than carrying decimal precision nothing produces.
    """

    __tablename__ = "blood_pressure_entries"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"), nullable=False, index=True)
    systolic: Mapped[int] = mapped_column(Integer, nullable=False)
    diastolic: Mapped[int] = mapped_column(Integer, nullable=False)
    pulse: Mapped[int | None] = mapped_column(Integer, nullable=True)
    recorded_date: Mapped[str] = mapped_column(String(10), nullable=False, index=True)  # YYYY-MM-DD
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)
