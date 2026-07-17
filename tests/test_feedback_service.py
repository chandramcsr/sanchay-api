from app.core.security import hash_password
from app.models.user import User
from app.services.feedback_service import submit_feedback


async def _make_user(db_session, suffix=""):
    user = User(email=f"alice-fbsvc{suffix}@example.com", hashed_password=hash_password("hunter2222"), display_name="Alice")
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)
    return user


async def test_submit_feedback_stores_the_real_users_id_and_email_not_anything_client_supplied(db_session):
    alice = await _make_user(db_session, "1")
    feedback = await submit_feedback(db_session, user=alice, category="bug", message="Something broke", app_version="10.81.1")
    assert feedback.user_id == alice.id
    assert feedback.email_snapshot == alice.email


async def test_submit_feedback_strips_leading_trailing_whitespace_from_the_message(db_session):
    alice = await _make_user(db_session, "2")
    feedback = await submit_feedback(db_session, user=alice, category="idea", message="  a suggestion  ", app_version=None)
    assert feedback.message == "a suggestion"


async def test_submit_feedback_rejects_an_invalid_category(db_session):
    alice = await _make_user(db_session, "3")
    try:
        await submit_feedback(db_session, user=alice, category="not-real", message="Test", app_version=None)
        assert False, "expected ValueError"
    except ValueError:
        pass
