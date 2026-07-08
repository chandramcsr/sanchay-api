from app.core.reset_tokens import generate_reset_token
from app.models.email_verification_token import EmailVerificationToken
from app.models.user import User


def _signup(client, email="verify-flow@example.com", password="hunter2222"):
    r = client.post("/auth/signup", json={"email": email, "password": password, "display_name": "Verify Flow"})
    return r.json()["access_token"]


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


def test_new_account_starts_unverified(client):
    token = _signup(client)
    me = client.get("/auth/me", headers=_auth(token))
    assert me.json()["is_verified"] is False


def test_unverified_account_can_still_sign_in_and_use_the_app(client):
    """The core product decision: verification is a nudge, not a gate."""
    _signup(client)
    r = client.post("/auth/login", json={"email": "verify-flow@example.com", "password": "hunter2222"})
    assert r.status_code == 200
    assert r.json()["user"]["is_verified"] is False


def test_signup_creates_a_verification_token(client, db_session):
    _signup(client)
    user = db_session.query(User).filter_by(email="verify-flow@example.com").first()
    tokens = db_session.query(EmailVerificationToken).filter_by(user_id=user.id).all()
    assert len(tokens) == 1


def test_verify_email_with_valid_token_marks_account_verified(client, db_session):
    _signup(client)
    user = db_session.query(User).filter_by(email="verify-flow@example.com").first()
    raw_token, token_hash = generate_reset_token()
    db_session.add(EmailVerificationToken(user_id=user.id, token_hash=token_hash))
    db_session.commit()

    r = client.post("/auth/verify-email", json={"token": raw_token})
    assert r.status_code == 200
    assert r.json()["is_verified"] is True


def test_verify_email_does_not_require_authentication(client, db_session):
    """Must work from a device with no session — e.g. clicking the link elsewhere."""
    _signup(client)
    user = db_session.query(User).filter_by(email="verify-flow@example.com").first()
    raw_token, token_hash = generate_reset_token()
    db_session.add(EmailVerificationToken(user_id=user.id, token_hash=token_hash))
    db_session.commit()

    r = client.post("/auth/verify-email", json={"token": raw_token})  # no Authorization header
    assert r.status_code == 200


def test_verify_email_rejects_invalid_token(client):
    r = client.post("/auth/verify-email", json={"token": "not-a-real-token"})
    assert r.status_code == 400


def test_verify_email_token_is_single_use(client, db_session):
    _signup(client)
    user = db_session.query(User).filter_by(email="verify-flow@example.com").first()
    raw_token, token_hash = generate_reset_token()
    db_session.add(EmailVerificationToken(user_id=user.id, token_hash=token_hash))
    db_session.commit()

    first = client.post("/auth/verify-email", json={"token": raw_token})
    assert first.status_code == 200
    second = client.post("/auth/verify-email", json={"token": raw_token})
    assert second.status_code == 400


def test_resend_verification_requires_authentication(client):
    r = client.post("/auth/resend-verification")
    assert r.status_code == 401


def test_resend_verification_sends_a_fresh_token(client, db_session):
    token = _signup(client)
    user = db_session.query(User).filter_by(email="verify-flow@example.com").first()

    r = client.post("/auth/resend-verification", headers=_auth(token))
    assert r.status_code == 200

    tokens = db_session.query(EmailVerificationToken).filter_by(user_id=user.id).all()
    assert len(tokens) == 2  # one from signup, one from resend


def test_resend_verification_is_a_no_op_for_already_verified_accounts(client, db_session):
    token = _signup(client)
    user = db_session.query(User).filter_by(email="verify-flow@example.com").first()
    raw_token, token_hash = generate_reset_token()
    db_session.add(EmailVerificationToken(user_id=user.id, token_hash=token_hash))
    db_session.commit()
    client.post("/auth/verify-email", json={"token": raw_token})

    r = client.post("/auth/resend-verification", headers=_auth(token))
    assert r.status_code == 200
    assert "already verified" in r.json()["message"].lower()


def test_resend_verification_is_rate_limited(client):
    token = _signup(client)
    for _ in range(3):
        client.post("/auth/resend-verification", headers=_auth(token))
    r = client.post("/auth/resend-verification", headers=_auth(token))
    assert r.status_code == 429
