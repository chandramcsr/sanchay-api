from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.health_profile import HealthProfile
from app.models.weight_entry import WeightEntry

VALID_SEXES = {"male", "female", "other", "prefer_not_to_say"}


async def get_profile(db: AsyncSession, *, user_id: str) -> HealthProfile | None:
    result = await db.execute(select(HealthProfile).where(HealthProfile.user_id == user_id))
    return result.scalar_one_or_none()


async def upsert_profile(
    db: AsyncSession, *, user_id: str,
    height_cm: float | None, date_of_birth: str | None, biological_sex: str | None, notes: str | None,
) -> HealthProfile:
    """
    Create-or-update, one row per user — matches the request shape
    (PUT, not POST), which is idempotent by design: submitting the
    same profile twice has the same effect as submitting it once.
    """
    if biological_sex is not None and biological_sex not in VALID_SEXES:
        raise ValueError(f"biological_sex must be one of {sorted(VALID_SEXES)}")

    profile = await get_profile(db, user_id=user_id)
    if profile is None:
        profile = HealthProfile(user_id=user_id)
        db.add(profile)

    stripped_notes = notes.strip() if notes else ""
    profile.height_cm = height_cm
    profile.date_of_birth = date_of_birth
    profile.biological_sex = biological_sex
    profile.notes = stripped_notes or None

    await db.commit()
    await db.refresh(profile)
    return profile


async def add_weight_entry(db: AsyncSession, *, user_id: str, weight_kg: float, recorded_date: str) -> WeightEntry:
    entry = WeightEntry(user_id=user_id, weight_kg=weight_kg, recorded_date=recorded_date)
    db.add(entry)
    await db.commit()
    await db.refresh(entry)
    return entry


async def list_weight_entries(db: AsyncSession, *, user_id: str) -> list[WeightEntry]:
    result = await db.execute(
        select(WeightEntry).where(WeightEntry.user_id == user_id).order_by(WeightEntry.recorded_date.desc())
    )
    return list(result.scalars().all())


async def delete_weight_entry(db: AsyncSession, *, user_id: str, entry_id: str) -> bool:
    """Returns False (not True/raise) when the entry doesn't exist or isn't this user's — the router turns that into a 404, same "not found, not 403" reasoning used throughout shared_expense_service."""
    result = await db.execute(select(WeightEntry).where(WeightEntry.id == entry_id, WeightEntry.user_id == user_id))
    entry = result.scalar_one_or_none()
    if entry is None:
        return False
    await db.delete(entry)
    await db.commit()
    return True


async def delete_health_references(db: AsyncSession, *, user_id: str) -> None:
    """
    Called by auth_service.delete_account() BEFORE the user row is
    deleted — explicit deletion, not freezing (see HealthProfile's
    docstring for why this domain's deletion policy is deliberately
    the opposite of shared-expenses'). Both health_profiles.user_id
    and weight_entries.user_id are NOT nullable, so without this call
    the final DELETE FROM users would hit the exact foreign-key
    violation already found and fixed for groups.created_by and
    Feedback.user_id — same bug class, caught proactively here rather
    than shipping a third instance of it.
    """
    result = await db.execute(select(HealthProfile).where(HealthProfile.user_id == user_id))
    for row in result.scalars().all():
        await db.delete(row)

    result = await db.execute(select(WeightEntry).where(WeightEntry.user_id == user_id))
    for row in result.scalars().all():
        await db.delete(row)

    await db.commit()
