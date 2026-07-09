from app.models.login_event import LoginEvent
from app.models.user import User
from tests.conftest import get_all, get_one


async def _signup(client, email="activity@example.com", password="hunter2222"):
    return await client.post("/api/v1/auth/signup", json={"email": email, "password": password, "display_name": "Activity Test"})


async def test_successful_login_creates_a_login_event(client, db_session):
    await _signup(client)
    await client.post("/api/v1/auth/login", json={"email": "activity@example.com", "password": "hunter2222"})

    events = await get_all(db_session, LoginEvent, email="activity@example.com")
    successes = [e for e in events if e.success]
    assert len(successes) == 1


async def test_failed_login_creates_a_login_event_too(client, db_session):
    await _signup(client)
    await client.post("/api/v1/auth/login", json={"email": "activity@example.com", "password": "wrongpassword1"})

    events = await get_all(db_session, LoginEvent, email="activity@example.com")
    failures = [e for e in events if not e.success]
    assert len(failures) == 1


async def test_failed_login_against_nonexistent_email_is_still_logged(client, db_session):
    """user_id is null (no real account), but the attempt itself is recorded."""
    await client.post("/api/v1/auth/login", json={"email": "nobody-here@example.com", "password": "whatever123"})

    event = await get_one(db_session, LoginEvent, email="nobody-here@example.com")
    assert event is not None
    assert event.success is False
    assert event.user_id is None


async def test_successful_login_updates_last_login_at(client, db_session):
    await _signup(client)
    await client.post("/api/v1/auth/login", json={"email": "activity@example.com", "password": "hunter2222"})

    user = await get_one(db_session, User, email="activity@example.com")
    assert user.last_login_at is not None


async def test_last_login_at_appears_on_me_endpoint(client):
    signup = await _signup(client)
    token = signup.json()["access_token"]
    await client.post("/api/v1/auth/login", json={"email": "activity@example.com", "password": "hunter2222"})

    me = await client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert me.json()["last_login_at"] is not None


async def test_login_history_requires_authentication(client):
    r = await client.get("/api/v1/auth/login-history")
    assert r.status_code == 401


async def test_login_history_returns_own_events_newest_first(client):
    signup = await _signup(client)
    token = signup.json()["access_token"]
    await client.post("/api/v1/auth/login", json={"email": "activity@example.com", "password": "wrongpass1"})
    await client.post("/api/v1/auth/login", json={"email": "activity@example.com", "password": "hunter2222"})

    r = await client.get("/api/v1/auth/login-history", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    events = r.json()
    assert len(events) >= 2
    # Newest first: the last call (the successful one) should lead.
    assert events[0]["success"] is True


async def test_login_history_does_not_leak_other_users_events(client):
    await _signup(client, email="userA@example.com")
    signupB = await _signup(client, email="userB@example.com")
    tokenB = signupB.json()["access_token"]

    await client.post("/api/v1/auth/login", json={"email": "userA@example.com", "password": "wrongpass1"})

    r = await client.get("/api/v1/auth/login-history", headers={"Authorization": f"Bearer {tokenB}"})
    emails_seen = [e for e in r.json()]
    # userB's history should only ever contain userB's own attempts —
    # the signup itself doesn't create a login_events row, so this
    # should simply be empty rather than containing userA's failure.
    assert len(emails_seen) == 0
