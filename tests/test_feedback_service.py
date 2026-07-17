from app.core.security import hash_password
from app.models.user import User
from app.services.feedback_service import freeze_feedback_references, submit_feedback


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


async def test_freeze_feedback_references_nulls_user_id_but_preserves_the_rest(db_session):
    alice = await _make_user(db_session, "4")
    feedback = await submit_feedback(db_session, user=alice, category="idea", message="A real suggestion", app_version="10.83.0")

    await freeze_feedback_references(db_session, user_id=alice.id)

    await db_session.refresh(feedback)
    assert feedback.user_id is None
    assert feedback.email_snapshot == alice.email  # attribution survives even though the account link is gone
    assert feedback.message == "A real suggestion"
    assert feedback.category == "idea"


async def test_deleting_an_account_that_submitted_feedback_succeeds_and_the_feedback_survives(db_session):
    """
    Caught proactively while fixing a related report (a non-nullable
    Group.created_by foreign key blocking account deletion) rather
    than waiting for a second one -- Feedback.user_id is nullable by
    design, but nothing actually nulled it before account deletion,
    so the same class of foreign-key violation would have blocked
    deleting an account that had ever submitted feedback.
    """
    from app.services import auth_service
    from app.repositories import user_repository

    alice = await _make_user(db_session, "5")
    feedback = await submit_feedback(db_session, user=alice, category="bug", message="Found a bug", app_version="10.83.0")

    await auth_service.delete_account(db_session, current_user=alice, password="hunter2222")

    await db_session.refresh(feedback)
    assert feedback.user_id is None
    assert feedback.email_snapshot == alice.email
    assert await user_repository.get_by_id(db_session, alice.id) is None
