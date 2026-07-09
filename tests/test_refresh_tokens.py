from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.core.reset_tokens import generate_reset_token
from app.models.refresh_token import RefreshToken
from app.models.user import User


async def _signup(client, email="refresh@example.com", password="hunter2222"):
    r = await client.post("/api/v1/auth/signup", json={"email": email, "password": password, "display_name": "Refresh Test"})
    return r.json()


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


async def test_signup_returns_both_access_and_refresh_tokens(client):
    body = await _signup(client)
    assert "access_token" in body
    assert "refresh_token" in body
    assert body["access_token"] != body["refresh_token"]


async def test_login_also_returns_a_refresh_token(client):
    await _signup(client)
    r = await client.post("/api/v1/auth/login", json={"email": "refresh@example.com", "password": "hunter2222"})
    assert "refresh_token" in r.json()


async def test_refresh_exchanges_a_valid_refresh_token_for_a_new_pair(client):
    body = await _signup(client)
    r = await client.post("/api/v1/auth/refresh", json={"refresh_token": body["refresh_token"]})
    assert r.status_code == 200
    new_body = r.json()
    # The refresh token is the meaningful thing to check here — it's
    # random (secrets.token_urlsafe), so it's guaranteed different on
    # every issuance. The access token is deliberately NOT asserted to
    # differ: JWTs are deterministic (no random nonce) — two tokens for
    # the same subject with the same second-granularity exp claim are
    # byte-identical, which is exactly what happens when signup and
    # this refresh call land in the same wall-clock second, as they
    # will in a fast test. That's not a security flaw (the token is
    # still valid and correctly scoped) — it just means "the access
    # token is byte-different" was never a real guarantee to test for.
    assert new_body["refresh_token"] != body["refresh_token"]


async def test_new_access_token_from_refresh_actually_works(client):
    body = await _signup(client)
    refresh_resp = await client.post("/api/v1/auth/refresh", json={"refresh_token": body["refresh_token"]})
    refreshed = refresh_resp.json()

    me = await client.get("/api/v1/auth/me", headers=_auth(refreshed["access_token"]))
    assert me.status_code == 200
    assert me.json()["email"] == "refresh@example.com"


async def test_refresh_token_is_single_use_rotation(client):
    """The core security property: a used refresh token can never be replayed."""
    body = await _signup(client)
    first_refresh = await client.post("/api/v1/auth/refresh", json={"refresh_token": body["refresh_token"]})
    assert first_refresh.status_code == 200

    # Replaying the ORIGINAL refresh token (from signup) must now fail —
    # it was revoked the moment it was used to get a new pair.
    replay = await client.post("/api/v1/auth/refresh", json={"refresh_token": body["refresh_token"]})
    assert replay.status_code == 401


async def test_refresh_rejects_a_garbage_token(client):
    r = await client.post("/api/v1/auth/refresh", json={"refresh_token": "not-a-real-token"})
    assert r.status_code == 401


async def test_refresh_rejects_an_expired_token(client, db_session):
    await _signup(client)
    result = await db_session.execute(select(User).filter_by(email="refresh@example.com"))
    user = result.scalar_one_or_none()

    raw_token, token_hash = generate_reset_token()
    expired = RefreshToken(
        user_id=user.id,
        token_hash=token_hash,
        expires_at=datetime.now(timezone.utc) - timedelta(days=1),
    )
    db_session.add(expired)
    await db_session.commit()

    r = await client.post("/api/v1/auth/refresh", json={"refresh_token": raw_token})
    assert r.status_code == 401


async def test_delete_account_removes_refresh_tokens(client, db_session):
    body = await _signup(client)
    result = await db_session.execute(select(User).filter_by(email="refresh@example.com"))
    user_id = result.scalar_one_or_none().id

    await client.request("DELETE", "/api/v1/auth/me", headers=_auth(body["access_token"]), json={"password": "hunter2222"})

    remaining_result = await db_session.execute(select(RefreshToken).filter_by(user_id=user_id))
    remaining = len(remaining_result.scalars().all())
    assert remaining == 0


async def test_refresh_is_rate_limited(client):
    body = await _signup(client)
    # 30/hour limit — well beyond normal use, but exhaustible in a test.
    current = body["refresh_token"]
    for _ in range(30):
        resp = await client.post("/api/v1/auth/refresh", json={"refresh_token": current})
        current = resp.json()["refresh_token"]
    r = await client.post("/api/v1/auth/refresh", json={"refresh_token": current})
    assert r.status_code == 429
