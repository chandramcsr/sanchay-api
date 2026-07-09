import os

os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-not-for-production-use")
os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")

from app.core.security import create_access_token, decode_access_token, hash_password, verify_password


async def test_hash_password_never_stores_plaintext():
    hashed = hash_password("hunter22")
    assert hashed != "hunter22"
    assert hashed.startswith("$2b$")  # bcrypt hash format


async def test_verify_password_round_trip():
    hashed = hash_password("hunter22")
    assert verify_password("hunter22", hashed) is True
    assert verify_password("wrongpassword", hashed) is False


async def test_hashing_the_same_password_twice_gives_different_hashes():
    # Each call uses a fresh salt — this is what defeats rainbow tables.
    assert hash_password("hunter22") != hash_password("hunter22")


async def test_verify_password_handles_malformed_hash_gracefully():
    assert verify_password("anything", "not-a-real-bcrypt-hash") is False


async def test_access_token_round_trip():
    token = create_access_token(subject="user-123")
    assert decode_access_token(token) == "user-123"


async def test_decode_rejects_garbage_token():
    assert decode_access_token("not.a.real.jwt") is None


async def test_decode_rejects_tampered_token():
    token = create_access_token(subject="user-123")
    tampered = token[:-2] + ("aa" if not token.endswith("aa") else "bb")
    assert decode_access_token(tampered) is None


async def test_expired_token_is_rejected():
    token = create_access_token(subject="user-123", expires_minutes=-1)
    assert decode_access_token(token) is None
