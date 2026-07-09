"""
Password hashing and JWT helpers.

Uses `bcrypt` directly rather than passlib.CryptContext: passlib 1.7.4
is incompatible with bcrypt >= 4.1 (it reads a removed __about__
attribute), a known, still-unfixed break in the passlib project. Calling
bcrypt directly is fewer moving parts and avoids the landmine entirely.
"""

from datetime import datetime, timedelta, timezone
import asyncio

import bcrypt
from jose import JWTError, jwt

from app.core.config import settings

BCRYPT_ROUNDS = 12


def hash_password(password: str) -> str:
    salt = bcrypt.gensalt(rounds=BCRYPT_ROUNDS)
    return bcrypt.hashpw(password.encode("utf-8"), salt).decode("utf-8")


def verify_password(password: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8"), hashed.encode("utf-8"))
    except ValueError:
        # Malformed hash (shouldn't happen with our own data) — fail closed.
        return False


async def hash_password_async(password: str) -> str:
    """
    bcrypt is deliberately slow (that's the point) and entirely
    CPU-bound — calling it directly from an async route would freeze
    the event loop for every other in-flight request for the duration
    of the hash, not just the caller's own. asyncio.to_thread hands it
    to a worker thread instead, so the event loop stays free to serve
    everyone else while this one request waits.
    """
    return await asyncio.to_thread(hash_password, password)


async def verify_password_async(password: str, hashed: str) -> bool:
    return await asyncio.to_thread(verify_password, password, hashed)


def create_access_token(subject: str, expires_minutes: int | None = None) -> str:
    expire = datetime.now(timezone.utc) + timedelta(
        minutes=expires_minutes or settings.access_token_expire_minutes
    )
    payload = {"sub": subject, "exp": expire}
    return jwt.encode(payload, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)


def decode_access_token(token: str) -> str | None:
    """Returns the subject (user id) if the token is valid, else None."""
    try:
        payload = jwt.decode(token, settings.jwt_secret_key, algorithms=[settings.jwt_algorithm])
        return payload.get("sub")
    except JWTError:
        return None
