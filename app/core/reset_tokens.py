import hashlib
import secrets

TOKEN_BYTES = 32  # 256 bits — matches JWT secret strength, not accidental


def generate_reset_token() -> tuple[str, str]:
    """Returns (raw_token_for_the_link, hash_to_store_in_the_db)."""
    raw = secrets.token_urlsafe(TOKEN_BYTES)
    return raw, hash_reset_token(raw)


def hash_reset_token(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()
