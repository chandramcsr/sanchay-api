"""
Password hashing and JWT, backed by jwt_library
(github.com/chandramcsr/jwt-library) — shared identity primitives
across services rather than duplicated per-repo.

This module stays as a thin, sanchay-api-specific wrapper: jwt_library
has no opinion on environment variable names or how a service stores
its secret, so it takes a JWTConfig explicitly. Built here, once, from
this service's own settings — and everything below re-exports under
the exact same names/signatures every existing caller in this
codebase already uses, so adopting the shared library required zero
changes to any caller.
"""

from jwt_library import JWTConfig
from jwt_library import create_access_token as _create_access_token
from jwt_library import decode_access_token as _decode_access_token
from jwt_library import hash_password, hash_password_async, verify_password, verify_password_async

from app.core.config import settings

_jwt_config = JWTConfig(
    secret_key=settings.jwt_secret_key,
    algorithm=settings.jwt_algorithm,
    default_expire_minutes=settings.access_token_expire_minutes,
)


def create_access_token(subject: str, expires_minutes: int | None = None) -> str:
    return _create_access_token(subject, _jwt_config, expires_minutes)


def decode_access_token(token: str) -> str | None:
    """Returns the subject (user id) if the token is valid, else None."""
    return _decode_access_token(token, _jwt_config)


__all__ = [
    "create_access_token",
    "decode_access_token",
    "hash_password",
    "hash_password_async",
    "verify_password",
    "verify_password_async",
]
