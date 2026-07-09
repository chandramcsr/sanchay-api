from sqlalchemy import delete as sa_delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.encrypted_ledger import EncryptedLedger


async def get_by_user_id(db: AsyncSession, user_id: str) -> EncryptedLedger | None:
    return await db.get(EncryptedLedger, user_id)


def create(db: AsyncSession, *, user_id: str, ciphertext: str, encryption_meta: str) -> EncryptedLedger:
    ledger = EncryptedLedger(user_id=user_id, ciphertext=ciphertext, encryption_meta=encryption_meta, version=1)
    db.add(ledger)
    return ledger


def update(ledger: EncryptedLedger, *, ciphertext: str, encryption_meta: str) -> EncryptedLedger:
    ledger.ciphertext = ciphertext
    ledger.encryption_meta = encryption_meta
    ledger.version += 1
    return ledger


async def delete_by_user_id(db: AsyncSession, user_id: str) -> None:
    await db.execute(sa_delete(EncryptedLedger).where(EncryptedLedger.user_id == user_id))
