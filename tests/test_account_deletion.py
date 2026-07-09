from app.models.encrypted_ledger import EncryptedLedger
from app.models.login_event import LoginEvent
from app.models.password_reset_token import PasswordResetToken
from app.models.user import User
from tests.conftest import count_rows, get_one


async def _signup(client, email="delete@example.com", password="hunter2222"):
    r = await client.post("/auth/signup", json={"email": email, "password": password, "display_name": "Delete Test"})
    return r.json()["access_token"]


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


async def test_delete_requires_authentication(client):
    r = await client.request("DELETE", "/auth/me", json={"password": "whatever123"})
    assert r.status_code == 401


async def test_delete_rejects_wrong_password(client):
    token = await _signup(client)
    r = await client.request("DELETE", "/auth/me", headers=_auth(token), json={"password": "wrongpassword1"})
    assert r.status_code == 401


async def test_delete_removes_the_user_row(client, db_session):
    token = await _signup(client)
    r = await client.request("DELETE", "/auth/me", headers=_auth(token), json={"password": "hunter2222"})
    assert r.status_code == 204

    user = await get_one(db_session, User, email="delete@example.com")
    assert user is None


async def test_deleted_account_can_no_longer_log_in(client):
    token = await _signup(client)
    await client.request("DELETE", "/auth/me", headers=_auth(token), json={"password": "hunter2222"})

    r = await client.post("/auth/login", json={"email": "delete@example.com", "password": "hunter2222"})
    assert r.status_code == 401


async def test_deleted_email_can_sign_up_again_fresh(client):
    token = await _signup(client)
    await client.request("DELETE", "/auth/me", headers=_auth(token), json={"password": "hunter2222"})

    r = await client.post("/auth/signup", json={"email": "delete@example.com", "password": "newpass123", "display_name": "Fresh Start"})
    assert r.status_code == 201


async def test_delete_removes_login_events(client, db_session):
    token = await _signup(client)
    await client.post("/auth/login", json={"email": "delete@example.com", "password": "wrongpass1"})  # a failure to log
    user = await get_one(db_session, User, email="delete@example.com")
    user_id = user.id

    await client.request("DELETE", "/auth/me", headers=_auth(token), json={"password": "hunter2222"})

    remaining = await count_rows(db_session, LoginEvent, user_id=user_id)
    assert remaining == 0


async def test_delete_removes_encrypted_sync_backup(client, db_session):
    token = await _signup(client)
    user = await get_one(db_session, User, email="delete@example.com")
    user_id = user.id
    await client.put("/sync/push", headers=_auth(token), json={
        "ciphertext": "secret-ledger-bytes", "encryption_meta": "m", "based_on_version": 0,
    })

    await client.request("DELETE", "/auth/me", headers=_auth(token), json={"password": "hunter2222"})

    remaining = await count_rows(db_session, EncryptedLedger, user_id=user_id)
    assert remaining == 0


async def test_delete_removes_password_reset_tokens(client, db_session):
    from app.core.reset_tokens import generate_reset_token

    token = await _signup(client)
    # Emails are lowercased server-side on signup — query with the
    # actual stored value, not the literal string passed in, since
    # SQLite's default string comparison is case-sensitive.
    user = await get_one(db_session, User, email="delete@example.com".lower())
    user_id = user.id  # captured now — after deletion, the ORM object itself
    # goes stale and raises errors on further attribute access
    _, token_hash = generate_reset_token()
    db_session.add(PasswordResetToken(user_id=user_id, token_hash=token_hash))
    await db_session.commit()

    await client.request("DELETE", "/auth/me", headers=_auth(token), json={"password": "hunter2222"})

    remaining = await count_rows(db_session, PasswordResetToken, user_id=user_id)
    assert remaining == 0


async def test_deleting_one_account_does_not_affect_another(client, db_session):
    """The one test that matters most: deletion is scoped to exactly one account."""
    tokenA = await _signup(client, email="userA-delete@example.com")
    await _signup(client, email="userB-delete@example.com")

    await client.request("DELETE", "/auth/me", headers=_auth(tokenA), json={"password": "hunter2222"})

    # Emails are lowercased server-side — query with the actual stored
    # value. (This case mismatch previously made this test FAIL with a
    # false "userB was deleted too" signal — verified via a standalone
    # repro that the endpoint itself scopes correctly; the bug was in
    # this assertion, not in delete_account().)
    userB = await get_one(db_session, User, email="userb-delete@example.com")
    assert userB is not None

    still_works = await client.post("/auth/login", json={"email": "userB-delete@example.com", "password": "hunter2222"})
    assert still_works.status_code == 200


async def test_delete_is_rate_limited(client):
    token = await _signup(client)
    for _ in range(3):
        await client.request("DELETE", "/auth/me", headers=_auth(token), json={"password": "wrongpass1"})
    r = await client.request("DELETE", "/auth/me", headers=_auth(token), json={"password": "wrongpass1"})
    assert r.status_code == 429


async def test_delete_removes_email_verification_tokens(client, db_session):
    from app.models.email_verification_token import EmailVerificationToken

    token = await _signup(client)
    user = await get_one(db_session, User, email="delete@example.com".lower())
    user_id = user.id
    # signup already created one verification token; add a second via resend
    await client.post("/auth/resend-verification", headers=_auth(token))

    await client.request("DELETE", "/auth/me", headers=_auth(token), json={"password": "hunter2222"})

    remaining = await count_rows(db_session, EmailVerificationToken, user_id=user_id)
    assert remaining == 0
