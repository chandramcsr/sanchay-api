from app.core.reset_tokens import generate_reset_token
from app.models.email_verification_token import EmailVerificationToken
from app.models.user import User
from tests.conftest import get_all, get_one


async def _signup(client, email="verify-flow@example.com", password="hunter2222"):
    r = await client.post("/auth/signup", json={"email": email, "password": password, "display_name": "Verify Flow"})
    return r.json()["access_token"]


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


async def test_new_account_starts_unverified(client):
    token = await _signup(client)
    me = await client.get("/auth/me", headers=_auth(token))
    assert me.json()["is_verified"] is False


async def test_unverified_account_can_still_sign_in_and_use_the_app(client):
    """The core product decision: verification is a nudge, not a gate."""
    await _signup(client)
    r = await client.post("/auth/login", json={"email": "verify-flow@example.com", "password": "hunter2222"})
    assert r.status_code == 200
    assert r.json()["user"]["is_verified"] is False


async def test_signup_creates_a_verification_token(client, db_session):
    await _signup(client)
    user = await get_one(db_session, User, email="verify-flow@example.com")
    tokens = await get_all(db_session, EmailVerificationToken, user_id=user.id)
    assert len(tokens) == 1


async def test_verify_email_with_valid_token_marks_account_verified(client, db_session):
    await _signup(client)
    user = await get_one(db_session, User, email="verify-flow@example.com")
    raw_token, token_hash = generate_reset_token()
    db_session.add(EmailVerificationToken(user_id=user.id, token_hash=token_hash))
    await db_session.commit()

    r = await client.post("/auth/verify-email", json={"token": raw_token})
    assert r.status_code == 200
    assert r.json()["is_verified"] is True


async def test_verify_email_does_not_require_authentication(client, db_session):
    """Must work from a device with no session — e.g. clicking the link elsewhere."""
    await _signup(client)
    user = await get_one(db_session, User, email="verify-flow@example.com")
    raw_token, token_hash = generate_reset_token()
    db_session.add(EmailVerificationToken(user_id=user.id, token_hash=token_hash))
    await db_session.commit()

    r = await client.post("/auth/verify-email", json={"token": raw_token})  # no Authorization header
    assert r.status_code == 200


async def test_verify_email_rejects_invalid_token(client):
    r = await client.post("/auth/verify-email", json={"token": "not-a-real-token"})
    assert r.status_code == 400


async def test_verify_email_token_is_single_use(client, db_session):
    await _signup(client)
    user = await get_one(db_session, User, email="verify-flow@example.com")
    raw_token, token_hash = generate_reset_token()
    db_session.add(EmailVerificationToken(user_id=user.id, token_hash=token_hash))
    await db_session.commit()

    first = await client.post("/auth/verify-email", json={"token": raw_token})
    assert first.status_code == 200
    second = await client.post("/auth/verify-email", json={"token": raw_token})
    assert second.status_code == 400


async def test_resend_verification_requires_authentication(client):
    r = await client.post("/auth/resend-verification")
    assert r.status_code == 401


async def test_resend_verification_sends_a_fresh_token(client, db_session):
    token = await _signup(client)
    user = await get_one(db_session, User, email="verify-flow@example.com")

    r = await client.post("/auth/resend-verification", headers=_auth(token))
    assert r.status_code == 200

    tokens = await get_all(db_session, EmailVerificationToken, user_id=user.id)
    assert len(tokens) == 2  # one from signup, one from resend


async def test_resend_verification_is_a_no_op_for_already_verified_accounts(client, db_session):
    token = await _signup(client)
    user = await get_one(db_session, User, email="verify-flow@example.com")
    raw_token, token_hash = generate_reset_token()
    db_session.add(EmailVerificationToken(user_id=user.id, token_hash=token_hash))
    await db_session.commit()
    await client.post("/auth/verify-email", json={"token": raw_token})

    r = await client.post("/auth/resend-verification", headers=_auth(token))
    assert r.status_code == 200
    assert "already verified" in r.json()["message"].lower()


async def test_resend_verification_is_rate_limited(client):
    token = await _signup(client)
    for _ in range(3):
        await client.post("/auth/resend-verification", headers=_auth(token))
    r = await client.post("/auth/resend-verification", headers=_auth(token))
    assert r.status_code == 429
