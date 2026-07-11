"""
Tests for the require_email_verification feature switch: when
enabled, login should refuse unverified accounts, but the refusal
must never distinguish "wrong password" from "no such account" (that
would be exactly the enumeration leak forgot_password() elsewhere in
this codebase is written to avoid) -- it may ONLY reveal "please
verify" after a password has already matched.

Also covers resend-verification-by-email, the unauthenticated escape
hatch this feature switch requires: once login can block an
unverified user, they may have no access token at all (e.g. closed
the app before verifying), so the existing authenticated
resend-verification endpoint becomes unreachable for exactly the
people who need it most.
"""

from app.core.config import settings
from app.models.email_verification_token import EmailVerificationToken
from app.models.user import User
from tests.conftest import get_all, get_one


async def _signup(client, email="switch-flow@example.com", password="hunter2222"):
    r = await client.post("/api/v1/auth/signup", json={"email": email, "password": password, "display_name": "Switch Flow"})
    return r.json()["access_token"]


# --- login gating ---------------------------------------------------


async def test_login_blocked_for_unverified_account_when_switch_on(client, monkeypatch):
    monkeypatch.setattr(settings, "require_email_verification", True)
    await _signup(client)

    r = await client.post("/api/v1/auth/login", json={"email": "switch-flow@example.com", "password": "hunter2222"})
    assert r.status_code == 403
    assert "verify" in r.json()["detail"].lower()
    assert "access_token" not in r.json()


async def test_login_still_works_for_unverified_account_when_switch_off(client):
    # Default/today's behavior, unaffected by this feature existing.
    await _signup(client)
    r = await client.post("/api/v1/auth/login", json={"email": "switch-flow@example.com", "password": "hunter2222"})
    assert r.status_code == 200


async def test_login_succeeds_for_verified_account_when_switch_on(client, db_session, monkeypatch):
    monkeypatch.setattr(settings, "require_email_verification", True)
    await _signup(client)
    user = await get_one(db_session, User, email="switch-flow@example.com")
    user.is_verified = True
    await db_session.commit()

    r = await client.post("/api/v1/auth/login", json={"email": "switch-flow@example.com", "password": "hunter2222"})
    assert r.status_code == 200
    assert "access_token" in r.json()


async def test_wrong_password_gives_same_generic_error_regardless_of_switch(client, monkeypatch):
    """
    The core enumeration-safety property: a WRONG password must never
    get the 403-verify response, even for a real unverified account --
    only a CORRECT password is allowed to reveal verification status.
    """
    monkeypatch.setattr(settings, "require_email_verification", True)
    await _signup(client)

    r = await client.post("/api/v1/auth/login", json={"email": "switch-flow@example.com", "password": "wrong-password-1"})
    assert r.status_code == 401
    assert r.json()["detail"] == "Incorrect email or password"


async def test_nonexistent_email_gives_identical_error_to_wrong_password(client, monkeypatch):
    """
    Same status code and same message body for "no such account" as
    for "wrong password on a real (possibly unverified) account" --
    an attacker must not be able to tell these apart, switch on or off.
    """
    monkeypatch.setattr(settings, "require_email_verification", True)
    await _signup(client)

    wrong_password = await client.post("/api/v1/auth/login", json={"email": "switch-flow@example.com", "password": "wrong-password-1"})
    no_account = await client.post("/api/v1/auth/login", json={"email": "never-signed-up@example.com", "password": "wrong-password-1"})

    assert wrong_password.status_code == no_account.status_code == 401
    assert wrong_password.json()["detail"] == no_account.json()["detail"]


async def test_switch_on_still_logs_a_successful_login_event_for_correct_password(client, db_session, monkeypatch):
    """
    A password-correct-but-unverified attempt is a real, successful
    authentication blocked by policy, not a failed login attempt --
    it should be logged as such (this matters for the brute-force
    visibility login_event_repository exists to provide).
    """
    from app.models.login_event import LoginEvent

    monkeypatch.setattr(settings, "require_email_verification", True)
    await _signup(client)
    await client.post("/api/v1/auth/login", json={"email": "switch-flow@example.com", "password": "hunter2222"})

    events = await get_all(db_session, LoginEvent, email="switch-flow@example.com")
    assert any(e.success for e in events)


# --- unauthenticated resend-verification-by-email --------------------


async def test_resend_by_email_sends_new_token_for_unverified_registered_email(client, db_session):
    await _signup(client)
    user = await get_one(db_session, User, email="switch-flow@example.com")

    r = await client.post("/api/v1/auth/resend-verification-by-email", json={"email": "switch-flow@example.com"})
    assert r.status_code == 200

    tokens = await get_all(db_session, EmailVerificationToken, user_id=user.id)
    assert len(tokens) == 2  # one from signup, one from this resend


async def test_resend_by_email_requires_no_authentication(client):
    """The whole point: reachable with zero session/token."""
    await _signup(client)
    r = await client.post("/api/v1/auth/resend-verification-by-email", json={"email": "switch-flow@example.com"})
    assert r.status_code == 200


async def test_resend_by_email_is_a_silent_no_op_for_unregistered_email(client, db_session):
    r = await client.post("/api/v1/auth/resend-verification-by-email", json={"email": "nobody-here@example.com"})
    assert r.status_code == 200
    tokens = await get_all(db_session, EmailVerificationToken)
    assert tokens == []


async def test_resend_by_email_response_is_identical_for_registered_and_unregistered(client):
    """Same status + same body either way -- this is the enumeration-safety property under test."""
    await _signup(client)
    registered = await client.post("/api/v1/auth/resend-verification-by-email", json={"email": "switch-flow@example.com"})
    unregistered = await client.post("/api/v1/auth/resend-verification-by-email", json={"email": "nobody-here@example.com"})

    assert registered.status_code == unregistered.status_code == 200
    assert registered.json() == unregistered.json()


async def test_resend_by_email_is_a_silent_no_op_for_already_verified_email(client, db_session):
    await _signup(client)
    user = await get_one(db_session, User, email="switch-flow@example.com")
    user.is_verified = True
    await db_session.commit()

    r = await client.post("/api/v1/auth/resend-verification-by-email", json={"email": "switch-flow@example.com"})
    assert r.status_code == 200
    tokens = await get_all(db_session, EmailVerificationToken, user_id=user.id)
    assert len(tokens) == 1  # only the one from signup -- no new one sent


async def test_resend_by_email_is_rate_limited(client):
    for _ in range(3):
        await client.post("/api/v1/auth/resend-verification-by-email", json={"email": "rate-limit-test@example.com"})
    r = await client.post("/api/v1/auth/resend-verification-by-email", json={"email": "rate-limit-test@example.com"})
    assert r.status_code == 429
