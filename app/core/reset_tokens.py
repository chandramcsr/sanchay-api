"""
Single-use token generation for password reset / email verification
links, backed by jwt_library's token primitives — same reasoning as
security.py: shared across services, wrapped here only to keep the
exact function names/signatures every existing caller already uses.
"""

from jwt_library import generate_token, hash_token


def generate_reset_token() -> tuple[str, str]:
    """Returns (raw_token_for_the_link, hash_to_store_in_the_db)."""
    raw = generate_token()
    return raw, hash_token(raw)


def hash_reset_token(raw: str) -> str:
    return hash_token(raw)
