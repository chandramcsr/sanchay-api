from datetime import datetime, timezone

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


def _now() -> datetime:
    return datetime.now(timezone.utc)


class EncryptedLedger(Base):
    """
    One encrypted blob per user — the whole ledger, pushed and pulled
    as a unit. This is sync v1, deliberately scoped: whole-blob
    replace, not a live merge of simultaneous edits from two devices.
    That's real, harder work (an operation log + CRDT-style merge) and
    is explicitly future scope, not attempted here.

    What this DOES guarantee, which matters more than it sounds:
    - True end-to-end encryption. `ciphertext` is opaque to this
      server — encrypted client-side with a passphrase that never
      leaves the device. A full database leak here hands an attacker
      random bytes, not anyone's financial data.
    - No silent data loss. `version` increments on every successful
      push; a push must declare which version it's replacing
      (`based_on_version` in the request), and the server rejects the
      write with 409 if that doesn't match current — meaning device A
      can never silently overwrite device B's newer, unseen changes.
      The client must pull the latest and reconcile before retrying.
    """

    __tablename__ = "encrypted_ledgers"

    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"), primary_key=True)
    ciphertext: Mapped[str] = mapped_column(Text, nullable=False)
    # Client-generated salt/IV metadata needed to decrypt — also
    # opaque to the server, just stored and returned alongside the
    # ciphertext so the client has what it needs to reverse the
    # encryption locally.
    encryption_meta: Mapped[str] = mapped_column(Text, nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now, nullable=False)
