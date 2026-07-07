from app.core.reset_tokens import generate_reset_token, hash_reset_token
from app.models.password_reset_token import PasswordResetToken
from app.models.user import User


def _signup(client, email="reset@example.com", password="hunter2222"):
    return client.post("/auth/signup", json={"email": email, "password": password, "display_name": "Reset Test"})


def test_forgot_password_returns_generic_message_for_existing_user(client):
    _signup(client)
    r = client.post("/auth/forgot-password", json={"email": "reset@example.com"})
    assert r.status_code == 200
    assert "reset link" in r.json()["message"].lower()


def test_forgot_password_returns_identical_message_for_nonexistent_email(client):
    """Prevents account enumeration via the forgot-password endpoint too."""
    _signup(client)
    real = client.post("/auth/forgot-password", json={"email": "reset@example.com"})
    fake = client.post("/auth/forgot-password", json={"email": "nobody-here@example.com"})
    assert real.status_code == fake.status_code == 200
    assert real.json() == fake.json()


def test_reset_password_with_invalid_token_is_rejected(client):
    r = client.post("/auth/reset-password", json={"token": "not-a-real-token", "new_password": "newpass123"})
    assert r.status_code == 400


def test_full_reset_flow_changes_password(client, db_session):
    _signup(client, password="oldpass123")
    user = db_session.query(User).filter_by(email="reset@example.com").first()

    # Simulate the token the "email" would have contained.
    raw_token, token_hash = generate_reset_token()
    db_session.add(PasswordResetToken(user_id=user.id, token_hash=token_hash))
    db_session.commit()

    reset = client.post("/auth/reset-password", json={"token": raw_token, "new_password": "brandnewpass123"})
    assert reset.status_code == 200
    assert "access_token" in reset.json()

    # Old password no longer works, new one does.
    old = client.post("/auth/login", json={"email": "reset@example.com", "password": "oldpass123"})
    assert old.status_code == 401
    new = client.post("/auth/login", json={"email": "reset@example.com", "password": "brandnewpass123"})
    assert new.status_code == 200


def test_reset_token_is_single_use(client, db_session):
    _signup(client, password="oldpass123")
    user = db_session.query(User).filter_by(email="reset@example.com").first()

    raw_token, token_hash = generate_reset_token()
    db_session.add(PasswordResetToken(user_id=user.id, token_hash=token_hash))
    db_session.commit()

    first = client.post("/auth/reset-password", json={"token": raw_token, "new_password": "firstnewpass1"})
    assert first.status_code == 200

    second = client.post("/auth/reset-password", json={"token": raw_token, "new_password": "secondnewpass1"})
    assert second.status_code == 400


def test_expired_reset_token_is_rejected(client, db_session):
    from datetime import datetime, timedelta, timezone

    _signup(client, password="oldpass123")
    user = db_session.query(User).filter_by(email="reset@example.com").first()

    raw_token, token_hash = generate_reset_token()
    expired = PasswordResetToken(
        user_id=user.id,
        token_hash=token_hash,
        expires_at=datetime.now(timezone.utc) - timedelta(minutes=1),
    )
    db_session.add(expired)
    db_session.commit()

    r = client.post("/auth/reset-password", json={"token": raw_token, "new_password": "newpassword1"})
    assert r.status_code == 400


def test_reset_password_rejects_weak_new_password(client, db_session):
    _signup(client, password="oldpass123")
    user = db_session.query(User).filter_by(email="reset@example.com").first()

    raw_token, token_hash = generate_reset_token()
    db_session.add(PasswordResetToken(user_id=user.id, token_hash=token_hash))
    db_session.commit()

    r = client.post("/auth/reset-password", json={"token": raw_token, "new_password": "short"})
    assert r.status_code == 422


def test_reset_token_hash_never_appears_in_forgot_password_response(client):
    """The raw token must only ever go out via the (currently logged) email — never in the HTTP response body."""
    _signup(client)
    r = client.post("/auth/forgot-password", json={"email": "reset@example.com"})
    assert "token" not in r.text.lower()


def test_forgot_password_is_rate_limited(client):
    for _ in range(3):
        client.post("/auth/forgot-password", json={"email": "ratelimited@example.com"})
    r = client.post("/auth/forgot-password", json={"email": "ratelimited@example.com"})
    assert r.status_code == 429
