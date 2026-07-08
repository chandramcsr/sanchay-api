from app.models.encrypted_ledger import EncryptedLedger
from app.models.login_event import LoginEvent
from app.models.password_reset_token import PasswordResetToken
from app.models.user import User


def _signup(client, email="delete@example.com", password="hunter2222"):
    r = client.post("/auth/signup", json={"email": email, "password": password, "display_name": "Delete Test"})
    return r.json()["access_token"]


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


def test_delete_requires_authentication(client):
    r = client.request("DELETE", "/auth/me", json={"password": "whatever123"})
    assert r.status_code == 401


def test_delete_rejects_wrong_password(client):
    token = _signup(client)
    r = client.request("DELETE", "/auth/me", headers=_auth(token), json={"password": "wrongpassword1"})
    assert r.status_code == 401


def test_delete_removes_the_user_row(client, db_session):
    token = _signup(client)
    r = client.request("DELETE", "/auth/me", headers=_auth(token), json={"password": "hunter2222"})
    assert r.status_code == 204

    user = db_session.query(User).filter_by(email="delete@example.com").first()
    assert user is None


def test_deleted_account_can_no_longer_log_in(client):
    token = _signup(client)
    client.request("DELETE", "/auth/me", headers=_auth(token), json={"password": "hunter2222"})

    r = client.post("/auth/login", json={"email": "delete@example.com", "password": "hunter2222"})
    assert r.status_code == 401


def test_deleted_email_can_sign_up_again_fresh(client):
    token = _signup(client)
    client.request("DELETE", "/auth/me", headers=_auth(token), json={"password": "hunter2222"})

    r = client.post("/auth/signup", json={"email": "delete@example.com", "password": "newpass123", "display_name": "Fresh Start"})
    assert r.status_code == 201


def test_delete_removes_login_events(client, db_session):
    token = _signup(client)
    client.post("/auth/login", json={"email": "delete@example.com", "password": "wrongpass1"})  # a failure to log
    user_id = db_session.query(User).filter_by(email="delete@example.com").first().id

    client.request("DELETE", "/auth/me", headers=_auth(token), json={"password": "hunter2222"})

    remaining = db_session.query(LoginEvent).filter_by(user_id=user_id).count()
    assert remaining == 0


def test_delete_removes_encrypted_sync_backup(client, db_session):
    token = _signup(client)
    user_id = db_session.query(User).filter_by(email="delete@example.com").first().id
    client.put("/sync/push", headers=_auth(token), json={
        "ciphertext": "secret-ledger-bytes", "encryption_meta": "m", "based_on_version": 0,
    })

    client.request("DELETE", "/auth/me", headers=_auth(token), json={"password": "hunter2222"})

    remaining = db_session.query(EncryptedLedger).filter_by(user_id=user_id).count()
    assert remaining == 0


def test_delete_removes_password_reset_tokens(client, db_session):
    from app.core.reset_tokens import generate_reset_token

    token = _signup(client)
    # Emails are lowercased server-side on signup — query with the
    # actual stored value, not the literal string passed in, since
    # SQLite's default string comparison is case-sensitive.
    user = db_session.query(User).filter_by(email="delete@example.com".lower()).first()
    user_id = user.id  # captured now — after deletion, the ORM object itself
    # goes stale and raises ObjectDeletedError on further attribute access
    _, token_hash = generate_reset_token()
    db_session.add(PasswordResetToken(user_id=user_id, token_hash=token_hash))
    db_session.commit()

    client.request("DELETE", "/auth/me", headers=_auth(token), json={"password": "hunter2222"})

    remaining = db_session.query(PasswordResetToken).filter_by(user_id=user_id).count()
    assert remaining == 0


def test_deleting_one_account_does_not_affect_another(client, db_session):
    """The one test that matters most: deletion is scoped to exactly one account."""
    tokenA = _signup(client, email="userA-delete@example.com")
    _signup(client, email="userB-delete@example.com")

    client.request("DELETE", "/auth/me", headers=_auth(tokenA), json={"password": "hunter2222"})

    # Emails are lowercased server-side — query with the actual stored
    # value. (This case mismatch previously made this test FAIL with a
    # false "userB was deleted too" signal — verified via a standalone
    # repro that the endpoint itself scopes correctly; the bug was in
    # this assertion, not in delete_account().)
    userB = db_session.query(User).filter_by(email="userb-delete@example.com").first()
    assert userB is not None

    still_works = client.post("/auth/login", json={"email": "userB-delete@example.com", "password": "hunter2222"})
    assert still_works.status_code == 200


def test_delete_is_rate_limited(client):
    token = _signup(client)
    for _ in range(3):
        client.request("DELETE", "/auth/me", headers=_auth(token), json={"password": "wrongpass1"})
    r = client.request("DELETE", "/auth/me", headers=_auth(token), json={"password": "wrongpass1"})
    assert r.status_code == 429


def test_delete_removes_email_verification_tokens(client, db_session):
    from app.core.reset_tokens import generate_reset_token
    from app.models.email_verification_token import EmailVerificationToken

    token = _signup(client)
    user_id = db_session.query(User).filter_by(email="delete@example.com".lower()).first().id
    # signup already created one verification token; add a second via resend
    client.post("/auth/resend-verification", headers=_auth(token))

    client.request("DELETE", "/auth/me", headers=_auth(token), json={"password": "hunter2222"})

    remaining = db_session.query(EmailVerificationToken).filter_by(user_id=user_id).count()
    assert remaining == 0
