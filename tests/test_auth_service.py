"""
These tests call auth_service functions directly — no TestClient, no
HTTP, no FastAPI request/response cycle at all. This is the concrete
payoff the Service/Repository refactor was for: business logic is now
testable as plain Python function calls, which is both faster and a
more precise way to test business rules than asserting on HTTP status
codes and JSON bodies for everything.

Not a replacement for the HTTP-level tests elsewhere (those prove the
routes are wired correctly, which this layer alone can't) — a
complement, testing the same logic from underneath.
"""

from fastapi import BackgroundTasks, HTTPException

from app.services import auth_service


async def test_signup_service_creates_user_and_returns_token(db_session):
    access_token, refresh_token, user = await auth_service.signup(
        db_session, BackgroundTasks(), email="direct@example.com", password="hunter2222", display_name="Direct Test"
    )
    assert user.email == "direct@example.com"
    assert user.display_name == "Direct Test"
    assert access_token  # a real access JWT
    assert refresh_token  # a real refresh token
    assert access_token != refresh_token


async def test_signup_service_rejects_duplicate_email(db_session):
    await auth_service.signup(
        db_session, BackgroundTasks(), email="dup@example.com", password="hunter2222", display_name="First"
    )
    try:
        await auth_service.signup(
            db_session, BackgroundTasks(), email="dup@example.com", password="different99", display_name="Second"
        )
        assert False, "expected HTTPException"
    except HTTPException as e:
        assert e.status_code == 400


async def test_login_service_rejects_wrong_password(db_session):
    await auth_service.signup(
        db_session, BackgroundTasks(), email="loginsvc@example.com", password="hunter2222", display_name="Login Svc"
    )

    class FakeRequest:
        client = None

    try:
        await auth_service.login(db_session, FakeRequest(), email="loginsvc@example.com", password="wrongpass1")
        assert False, "expected HTTPException"
    except HTTPException as e:
        assert e.status_code == 401


async def test_delete_account_service_requires_correct_password(db_session):
    _, _, user = await auth_service.signup(
        db_session, BackgroundTasks(), email="deletesvc@example.com", password="hunter2222", display_name="Delete Svc"
    )
    try:
        await auth_service.delete_account(db_session, current_user=user, password="wrongpass1")
        assert False, "expected HTTPException"
    except HTTPException as e:
        assert e.status_code == 401
