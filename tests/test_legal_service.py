from app.core.security import hash_password
from app.models.user import User
from app.services import legal_service


async def _make_user(db_session, suffix=""):
    user = User(email=f"alice-legal{suffix}@example.com", hashed_password=hash_password("hunter2222"), display_name="Alice")
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)
    return user


async def test_create_record_succeeds_for_every_valid_record_type(db_session):
    alice = await _make_user(db_session, "1")
    valid_types = ["will", "nomination", "contract", "financial_dispute", "insurance_claim", "property_document", "compliance_deadline"]
    for i, rt in enumerate(valid_types):
        record = await legal_service.create_record(
            db_session, user_id=alice.id, record_type=rt, title=f"Test {rt}", status="active",
            key_date="2026-08-01", amount=None, counterparty=None, document_location=None, notes=None,
        )
        assert record.record_type == rt

    records = await legal_service.list_records(db_session, user_id=alice.id)
    assert len(records) == len(valid_types)


async def test_create_record_rejects_an_invalid_record_type(db_session):
    alice = await _make_user(db_session, "2")
    try:
        await legal_service.create_record(
            db_session, user_id=alice.id, record_type="not-a-real-type", title="Test", status=None,
            key_date=None, amount=None, counterparty=None, document_location=None, notes=None,
        )
        assert False, "expected ValueError"
    except ValueError:
        pass


async def test_create_record_stores_all_optional_fields(db_session):
    alice = await _make_user(db_session, "3")
    record = await legal_service.create_record(
        db_session, user_id=alice.id, record_type="insurance_claim", title="Car accident claim",
        status="pending", key_date="2026-09-15", amount=50000.0, counterparty="ICICI Lombard",
        document_location="Email folder: Insurance", notes="  Filed after the July accident  ",
    )
    assert record.amount == 50000.0
    assert record.counterparty == "ICICI Lombard"
    assert record.document_location == "Email folder: Insurance"
    assert record.notes == "Filed after the July accident"  # stripped


async def test_create_record_treats_blank_notes_as_none(db_session):
    alice = await _make_user(db_session, "4")
    record = await legal_service.create_record(
        db_session, user_id=alice.id, record_type="will", title="My will", status=None,
        key_date=None, amount=None, counterparty=None, document_location=None, notes="   ",
    )
    assert record.notes is None


async def test_list_records_orders_by_key_date_with_nulls_last(db_session):
    alice = await _make_user(db_session, "5")
    await legal_service.create_record(db_session, user_id=alice.id, record_type="contract", title="No date", status=None, key_date=None, amount=None, counterparty=None, document_location=None, notes=None)
    await legal_service.create_record(db_session, user_id=alice.id, record_type="contract", title="Later", status=None, key_date="2026-12-01", amount=None, counterparty=None, document_location=None, notes=None)
    await legal_service.create_record(db_session, user_id=alice.id, record_type="contract", title="Sooner", status=None, key_date="2026-08-01", amount=None, counterparty=None, document_location=None, notes=None)

    records = await legal_service.list_records(db_session, user_id=alice.id)
    assert [r.title for r in records] == ["Sooner", "Later", "No date"]


async def test_list_records_filters_by_record_type(db_session):
    alice = await _make_user(db_session, "6")
    await legal_service.create_record(db_session, user_id=alice.id, record_type="will", title="Will", status=None, key_date=None, amount=None, counterparty=None, document_location=None, notes=None)
    await legal_service.create_record(db_session, user_id=alice.id, record_type="contract", title="Lease", status=None, key_date=None, amount=None, counterparty=None, document_location=None, notes=None)

    wills = await legal_service.list_records(db_session, user_id=alice.id, record_type="will")
    assert len(wills) == 1
    assert wills[0].title == "Will"


async def test_records_are_scoped_per_user(db_session):
    alice = await _make_user(db_session, "7")
    bob = await _make_user(db_session, "7b")
    await legal_service.create_record(db_session, user_id=alice.id, record_type="will", title="Alice's will", status=None, key_date=None, amount=None, counterparty=None, document_location=None, notes=None)
    await legal_service.create_record(db_session, user_id=bob.id, record_type="will", title="Bob's will", status=None, key_date=None, amount=None, counterparty=None, document_location=None, notes=None)

    alice_records = await legal_service.list_records(db_session, user_id=alice.id)
    assert len(alice_records) == 1
    assert alice_records[0].title == "Alice's will"


async def test_update_record_succeeds_for_the_owner(db_session):
    alice = await _make_user(db_session, "8")
    record = await legal_service.create_record(db_session, user_id=alice.id, record_type="contract", title="Lease v1", status="active", key_date="2026-08-01", amount=None, counterparty=None, document_location=None, notes=None)

    updated = await legal_service.update_record(
        db_session, user_id=alice.id, record_id=record.id, title="Lease v2", status="renewed",
        key_date="2027-08-01", amount=25000.0, counterparty="Landlord", document_location="Drive", notes=None,
    )
    assert updated is not None
    assert updated.title == "Lease v2"
    assert updated.status == "renewed"
    assert updated.amount == 25000.0


async def test_update_record_returns_none_for_someone_elses_record(db_session):
    alice = await _make_user(db_session, "9")
    bob = await _make_user(db_session, "9b")
    record = await legal_service.create_record(db_session, user_id=bob.id, record_type="will", title="Bob's will", status=None, key_date=None, amount=None, counterparty=None, document_location=None, notes=None)

    result = await legal_service.update_record(
        db_session, user_id=alice.id, record_id=record.id, title="Hijacked", status=None,
        key_date=None, amount=None, counterparty=None, document_location=None, notes=None,
    )
    assert result is None

    # Bob's record is untouched.
    bob_records = await legal_service.list_records(db_session, user_id=bob.id)
    assert bob_records[0].title == "Bob's will"


async def test_delete_record_removes_it_and_returns_true(db_session):
    alice = await _make_user(db_session, "10")
    record = await legal_service.create_record(db_session, user_id=alice.id, record_type="will", title="My will", status=None, key_date=None, amount=None, counterparty=None, document_location=None, notes=None)

    found = await legal_service.delete_record(db_session, user_id=alice.id, record_id=record.id)
    assert found is True
    assert await legal_service.list_records(db_session, user_id=alice.id) == []


async def test_delete_record_returns_false_for_someone_elses_record(db_session):
    alice = await _make_user(db_session, "11")
    bob = await _make_user(db_session, "11b")
    record = await legal_service.create_record(db_session, user_id=bob.id, record_type="will", title="Bob's will", status=None, key_date=None, amount=None, counterparty=None, document_location=None, notes=None)

    found = await legal_service.delete_record(db_session, user_id=alice.id, record_id=record.id)
    assert found is False
    assert len(await legal_service.list_records(db_session, user_id=bob.id)) == 1


async def test_delete_legal_records_removes_everything_for_the_user(db_session):
    alice = await _make_user(db_session, "12")
    await legal_service.create_record(db_session, user_id=alice.id, record_type="will", title="Will", status=None, key_date=None, amount=None, counterparty=None, document_location=None, notes=None)
    await legal_service.create_record(db_session, user_id=alice.id, record_type="contract", title="Lease", status=None, key_date=None, amount=None, counterparty=None, document_location=None, notes=None)

    await legal_service.delete_legal_records(db_session, user_id=alice.id)

    assert await legal_service.list_records(db_session, user_id=alice.id) == []


async def test_deleting_an_account_with_legal_records_succeeds_and_removes_them(db_session):
    """
    Same bug class already found and fixed three times this session
    (Group.created_by, Feedback.user_id, health tables) -- verified
    directly again here rather than assumed to work just because
    delete_legal_records() exists and is wired in. legal_records.user_id
    is NOT nullable, so this reproduces the exact failure mode if the
    wiring is ever removed or reordered.
    """
    from app.services import auth_service
    from app.repositories import user_repository

    alice = await _make_user(db_session, "13")
    await legal_service.create_record(db_session, user_id=alice.id, record_type="will", title="My will", status=None, key_date=None, amount=None, counterparty=None, document_location=None, notes=None)

    await auth_service.delete_account(db_session, current_user=alice, password="hunter2222")

    assert await user_repository.get_by_id(db_session, alice.id) is None
    assert await legal_service.list_records(db_session, user_id=alice.id) == []
