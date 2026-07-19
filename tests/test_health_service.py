from app.core.security import hash_password
from app.models.user import User
from app.services import health_service


async def _make_user(db_session, suffix=""):
    user = User(email=f"alice-health{suffix}@example.com", hashed_password=hash_password("hunter2222"), display_name="Alice")
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)
    return user


async def test_get_profile_returns_none_when_nothing_saved_yet(db_session):
    alice = await _make_user(db_session, "1")
    assert await health_service.get_profile(db_session, user_id=alice.id) is None


async def test_upsert_profile_creates_on_first_call(db_session):
    alice = await _make_user(db_session, "2")
    profile = await health_service.upsert_profile(
        db_session, user_id=alice.id, height_cm=170.0, age=35,
        biological_sex="female", notes="No known allergies",
    )
    assert profile.height_cm == 170.0
    assert profile.age == 35
    assert profile.biological_sex == "female"
    assert profile.notes == "No known allergies"


async def test_upsert_profile_updates_the_same_row_on_a_second_call(db_session):
    alice = await _make_user(db_session, "3")
    await health_service.upsert_profile(
        db_session, user_id=alice.id, height_cm=170.0, age=35,
        biological_sex="female", notes=None,
    )
    await health_service.upsert_profile(
        db_session, user_id=alice.id, height_cm=171.0, age=35,
        biological_sex="female", notes="Updated",
    )
    profile = await health_service.get_profile(db_session, user_id=alice.id)
    assert profile.height_cm == 171.0
    assert profile.notes == "Updated"

    # Exactly one row -- an upsert, not an accumulating history.
    from sqlalchemy import select
    from app.models.health_profile import HealthProfile
    result = await db_session.execute(select(HealthProfile).where(HealthProfile.user_id == alice.id))
    assert len(result.scalars().all()) == 1


async def test_upsert_profile_rejects_an_invalid_biological_sex(db_session):
    alice = await _make_user(db_session, "4")
    try:
        await health_service.upsert_profile(
            db_session, user_id=alice.id, height_cm=170.0, age=None,
            biological_sex="not-a-real-option", notes=None,
        )
        assert False, "expected ValueError"
    except ValueError:
        pass


async def test_upsert_profile_strips_notes_whitespace_and_treats_blank_as_none(db_session):
    alice = await _make_user(db_session, "5")
    profile = await health_service.upsert_profile(
        db_session, user_id=alice.id, height_cm=None, age=None, biological_sex=None, notes="   ",
    )
    assert profile.notes is None


async def test_add_and_list_weight_entries_most_recent_first(db_session):
    alice = await _make_user(db_session, "6")
    await health_service.add_weight_entry(db_session, user_id=alice.id, weight_kg=70.0, recorded_date="2026-07-01")
    await health_service.add_weight_entry(db_session, user_id=alice.id, weight_kg=69.5, recorded_date="2026-07-10")
    await health_service.add_weight_entry(db_session, user_id=alice.id, weight_kg=69.8, recorded_date="2026-07-05")

    entries = await health_service.list_weight_entries(db_session, user_id=alice.id)
    assert [e.recorded_date for e in entries] == ["2026-07-10", "2026-07-05", "2026-07-01"]


async def test_weight_entries_are_scoped_per_user(db_session):
    alice = await _make_user(db_session, "7")
    bob = await _make_user(db_session, "7b")
    await health_service.add_weight_entry(db_session, user_id=alice.id, weight_kg=70.0, recorded_date="2026-07-01")
    await health_service.add_weight_entry(db_session, user_id=bob.id, weight_kg=80.0, recorded_date="2026-07-01")

    alice_entries = await health_service.list_weight_entries(db_session, user_id=alice.id)
    assert len(alice_entries) == 1
    assert alice_entries[0].weight_kg == 70.0


async def test_delete_weight_entry_removes_it_and_returns_true(db_session):
    alice = await _make_user(db_session, "8")
    entry = await health_service.add_weight_entry(db_session, user_id=alice.id, weight_kg=70.0, recorded_date="2026-07-01")

    found = await health_service.delete_weight_entry(db_session, user_id=alice.id, entry_id=entry.id)
    assert found is True
    assert await health_service.list_weight_entries(db_session, user_id=alice.id) == []


async def test_delete_weight_entry_returns_false_for_someone_elses_entry(db_session):
    """Not found, not 403 -- same reasoning as the shared-expenses module: don't confirm to the caller that a resource exists at all if it isn't theirs."""
    alice = await _make_user(db_session, "9")
    bob = await _make_user(db_session, "9b")
    entry = await health_service.add_weight_entry(db_session, user_id=bob.id, weight_kg=80.0, recorded_date="2026-07-01")

    found = await health_service.delete_weight_entry(db_session, user_id=alice.id, entry_id=entry.id)
    assert found is False
    # Bob's entry is untouched.
    assert len(await health_service.list_weight_entries(db_session, user_id=bob.id)) == 1


async def test_delete_health_references_removes_both_profile_and_weight_entries(db_session):
    alice = await _make_user(db_session, "10")
    await health_service.upsert_profile(db_session, user_id=alice.id, height_cm=170.0, age=None, biological_sex=None, notes=None)
    await health_service.add_weight_entry(db_session, user_id=alice.id, weight_kg=70.0, recorded_date="2026-07-01")
    await health_service.add_weight_entry(db_session, user_id=alice.id, weight_kg=69.5, recorded_date="2026-07-10")

    await health_service.delete_health_references(db_session, user_id=alice.id)

    assert await health_service.get_profile(db_session, user_id=alice.id) is None
    assert await health_service.list_weight_entries(db_session, user_id=alice.id) == []


async def test_deleting_an_account_with_health_data_succeeds_and_removes_it(db_session):
    """
    Same bug class already found twice this session (Group.created_by,
    Feedback.user_id) -- both health_profiles.user_id and
    weight_entries.user_id are NOT nullable, so this must be verified
    directly rather than assumed safe just because
    delete_health_references() exists: it has to actually be CALLED,
    in the right order, for the foreign key to never be violated.
    """
    from app.services import auth_service
    from app.repositories import user_repository

    alice = await _make_user(db_session, "11")
    await health_service.upsert_profile(db_session, user_id=alice.id, height_cm=170.0, age=35, biological_sex="female", notes=None)
    await health_service.add_weight_entry(db_session, user_id=alice.id, weight_kg=70.0, recorded_date="2026-07-01")

    await auth_service.delete_account(db_session, current_user=alice, password="hunter2222")

    assert await user_repository.get_by_id(db_session, alice.id) is None
    assert await health_service.get_profile(db_session, user_id=alice.id) is None
    assert await health_service.list_weight_entries(db_session, user_id=alice.id) == []


async def test_add_and_list_blood_pressure_entries_most_recent_first(db_session):
    alice = await _make_user(db_session, "12")
    await health_service.add_blood_pressure_entry(db_session, user_id=alice.id, systolic=120, diastolic=80, pulse=70, recorded_date="2026-07-01")
    await health_service.add_blood_pressure_entry(db_session, user_id=alice.id, systolic=118, diastolic=78, pulse=68, recorded_date="2026-07-10")
    await health_service.add_blood_pressure_entry(db_session, user_id=alice.id, systolic=122, diastolic=82, pulse=72, recorded_date="2026-07-05")

    entries = await health_service.list_blood_pressure_entries(db_session, user_id=alice.id)
    assert [e.recorded_date for e in entries] == ["2026-07-10", "2026-07-05", "2026-07-01"]


async def test_blood_pressure_entry_pulse_is_optional(db_session):
    alice = await _make_user(db_session, "13")
    entry = await health_service.add_blood_pressure_entry(db_session, user_id=alice.id, systolic=120, diastolic=80, pulse=None, recorded_date="2026-07-01")
    assert entry.pulse is None
    assert entry.systolic == 120
    assert entry.diastolic == 80


async def test_blood_pressure_entries_are_scoped_per_user(db_session):
    alice = await _make_user(db_session, "14")
    bob = await _make_user(db_session, "14b")
    await health_service.add_blood_pressure_entry(db_session, user_id=alice.id, systolic=120, diastolic=80, pulse=70, recorded_date="2026-07-01")
    await health_service.add_blood_pressure_entry(db_session, user_id=bob.id, systolic=130, diastolic=85, pulse=75, recorded_date="2026-07-01")

    alice_entries = await health_service.list_blood_pressure_entries(db_session, user_id=alice.id)
    assert len(alice_entries) == 1
    assert alice_entries[0].systolic == 120


async def test_delete_blood_pressure_entry_removes_it_and_returns_true(db_session):
    alice = await _make_user(db_session, "15")
    entry = await health_service.add_blood_pressure_entry(db_session, user_id=alice.id, systolic=120, diastolic=80, pulse=70, recorded_date="2026-07-01")

    found = await health_service.delete_blood_pressure_entry(db_session, user_id=alice.id, entry_id=entry.id)
    assert found is True
    assert await health_service.list_blood_pressure_entries(db_session, user_id=alice.id) == []


async def test_delete_blood_pressure_entry_returns_false_for_someone_elses_entry(db_session):
    alice = await _make_user(db_session, "16")
    bob = await _make_user(db_session, "16b")
    entry = await health_service.add_blood_pressure_entry(db_session, user_id=bob.id, systolic=130, diastolic=85, pulse=None, recorded_date="2026-07-01")

    found = await health_service.delete_blood_pressure_entry(db_session, user_id=alice.id, entry_id=entry.id)
    assert found is False
    assert len(await health_service.list_blood_pressure_entries(db_session, user_id=bob.id)) == 1


async def test_delete_health_references_also_removes_blood_pressure_entries(db_session):
    alice = await _make_user(db_session, "17")
    await health_service.add_blood_pressure_entry(db_session, user_id=alice.id, systolic=120, diastolic=80, pulse=70, recorded_date="2026-07-01")

    await health_service.delete_health_references(db_session, user_id=alice.id)

    assert await health_service.list_blood_pressure_entries(db_session, user_id=alice.id) == []


async def test_deleting_an_account_with_blood_pressure_data_succeeds_and_removes_it(db_session):
    """
    Same bug class already found and fixed twice this session before
    this table even existed (Group.created_by, Feedback.user_id) --
    blood_pressure_entries.user_id is ALSO not nullable, so this is
    verified directly again here rather than assuming
    delete_health_references()'s existing coverage of weight/profile
    automatically extends to a table added after it was written.
    """
    from app.services import auth_service
    from app.repositories import user_repository

    alice = await _make_user(db_session, "18")
    await health_service.add_blood_pressure_entry(db_session, user_id=alice.id, systolic=120, diastolic=80, pulse=70, recorded_date="2026-07-01")

    await auth_service.delete_account(db_session, current_user=alice, password="hunter2222")

    assert await user_repository.get_by_id(db_session, alice.id) is None
    assert await health_service.list_blood_pressure_entries(db_session, user_id=alice.id) == []
