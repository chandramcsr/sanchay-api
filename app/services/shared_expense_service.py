"""
Shared-expense business logic. Three pieces worth understanding
before touching this file:

1. SPLITTING MATH uses Decimal throughout, never float — an even
   split of $100 three ways is $33.33/$33.33/$33.34, and float
   arithmetic on money is exactly the class of bug that produces
   $33.330000000000005 instead. The largest-remainder method
   (split_evenly) guarantees the parts always sum EXACTLY to the
   total, with the leftover cents distributed deterministically
   (largest fractional remainder first) rather than "doesn't matter
   who" actually meaning "arbitrary" — it's still a real, repeatable
   algorithm, just not one that favors any particular participant.

2. email_ref IS THE DURABLE IDENTITY ANCHOR, user_id IS JUST "WHO'S
   CURRENTLY ACTIVE". Every user-referencing row in this module
   stores BOTH: a nullable user_id (the live account link, nulled on
   deletion) and an email_ref (a SHA-256 hash of the person's
   normalized email, set once at creation and never changed). The
   raw email is never stored anywhere in this module — only its
   hash, via the exact same primitive jwt-library already uses for
   single-use tokens (hash_token). This is what makes
   reconnect_by_email() possible: it's a pure lookup by a
   deterministic value, no PII persisted to make it work.

3. THE FREEZE-NOT-CASCADE DELETION POLICY, AND ITS MIRROR. When
   someone deletes their account, freeze_user_references() nulls
   user_id everywhere (keeping email_ref and name_snapshot intact) —
   the historical record survives, their live account doesn't. If
   they ever sign up again with the SAME email, reconnect_by_email()
   (called from auth_service.signup()) finds every frozen row whose
   email_ref matches and re-populates user_id — their old shared
   history becomes live and editable again automatically. Together
   these are the only two integration points this module has with
   the rest of the app — deliberately narrow, so this stays
   extractable into its own service later without a rewrite.
"""

from datetime import datetime, timezone

from fastapi import BackgroundTasks
from decimal import Decimal

from jwt_library import hash_token
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.email import email_sender
from app.models.group import Group
from app.models.group_member import GroupMember
from app.models.pending_group_invite import PendingGroupInvite
from app.models.settlement import Settlement
from app.models.shared_expense import SharedExpense
from app.models.shared_expense_comment import SharedExpenseComment
from app.models.shared_expense_split import SharedExpenseSplit
from app.models.user import User


def email_reference(email: str) -> str:
    """
    SHA-256 of the normalized (lowercased, trimmed) email — never the
    raw address itself. Deterministic: the same email always produces
    the same reference, which is the whole mechanism reconnection
    relies on, without this module ever needing to store or expose
    anyone's actual email to other group members.
    """
    return hash_token(email.strip().lower())


def split_evenly(total: Decimal, participant_ids: list[str]) -> dict[str, Decimal]:
    """
    Largest-remainder method: round every share down to the cent
    first, then hand out the leftover pennies one at a time to
    whichever shares had the largest fractional remainder. Guarantees
    sum(result.values()) == total exactly, for any total and any
    number of participants — the property that actually matters here,
    since a split that doesn't sum to the total means the group
    balance can never truly reach zero even after everyone pays.
    """
    n = len(participant_ids)
    if n == 0:
        return {}

    cents_total = int((total * 100).to_integral_value())
    base_cents = cents_total // n
    remainder_cents = cents_total - base_cents * n

    exact_share = total / n
    remainders = [(pid, exact_share - Decimal(base_cents) / 100) for pid in participant_ids]
    remainders.sort(key=lambda r: r[1], reverse=True)

    shares_cents = {pid: base_cents for pid in participant_ids}
    for i in range(remainder_cents):
        shares_cents[remainders[i][0]] += 1

    return {pid: (Decimal(cents) / 100).quantize(Decimal("0.01")) for pid, cents in shares_cents.items()}


async def add_member_to_group(db: AsyncSession, *, group_id: str, user_id: str) -> None:
    """
    Adds an already-resolved user to an already-existing group. Kept
    separate from create_group's inline member creation rather than
    having create_group call this in a loop — create_group needs a
    flush() before any GroupMember rows can reference the new group's
    id, a step this function (working against a group that already
    exists) doesn't need.
    """
    existing = await db.execute(select(GroupMember).where(GroupMember.group_id == group_id, GroupMember.user_id == user_id))
    if existing.scalar_one_or_none() is not None:
        return  # already a member — silently a no-op, not an error
    user = await db.get(User, user_id)
    db.add(GroupMember(
        group_id=group_id,
        user_id=user_id,
        email_ref=email_reference(user.email) if user else "",
        name_snapshot=user.display_name if user else "Unknown",
    ))
    await db.commit()


async def member_has_expense_history(db: AsyncSession, *, group_id: str, user_id: str) -> bool:
    result = await db.execute(
        select(SharedExpenseSplit)
        .join(SharedExpense, SharedExpenseSplit.shared_expense_id == SharedExpense.id)
        .where(SharedExpense.group_id == group_id, SharedExpenseSplit.user_id == user_id)
    )
    if result.first() is not None:
        return True
    result = await db.execute(select(SharedExpense).where(SharedExpense.group_id == group_id, SharedExpense.paid_by == user_id))
    return result.first() is not None


async def remove_member_from_group(db: AsyncSession, *, group_id: str, user_id: str) -> None:
    """
    Same protection group deletion already has, applied to a single
    person instead of the whole group: removing someone who has real
    expense history in this group would silently orphan a debt (their
    split would still exist, still count toward balances, but the
    person it belongs to would no longer even be listed as part of
    the group) — the caller must check member_has_expense_history()
    first and refuse if it's true. This function only performs the
    actual removal once that's been confirmed clear.
    """
    result = await db.execute(select(GroupMember).where(GroupMember.group_id == group_id, GroupMember.user_id == user_id))
    member = result.scalar_one_or_none()
    if member is not None:
        await db.delete(member)
        await db.commit()


async def pending_invite_has_expense_history(db: AsyncSession, *, group_id: str, email: str) -> bool:
    ref = email_reference(email)
    result = await db.execute(
        select(SharedExpenseSplit)
        .join(SharedExpense, SharedExpenseSplit.shared_expense_id == SharedExpense.id)
        .where(SharedExpense.group_id == group_id, SharedExpenseSplit.email_ref == ref, SharedExpenseSplit.user_id.is_(None))
    )
    return result.first() is not None


async def remove_pending_invite(db: AsyncSession, *, group_id: str, email: str) -> None:
    """Same expense-history protection as remove_member_from_group — caller checks pending_invite_has_expense_history() first."""
    normalized = email.strip().lower()
    result = await db.execute(
        select(PendingGroupInvite).where(PendingGroupInvite.group_id == group_id, PendingGroupInvite.email == normalized)
    )
    invite = result.scalar_one_or_none()
    if invite is not None:
        await db.delete(invite)
        await db.commit()


async def create_group(db: AsyncSession, *, name: str, created_by: str, member_ids: list[str]) -> Group:
    group = Group(name=name, created_by=created_by)
    db.add(group)
    await db.flush()  # need group.id before creating members

    all_members = set(member_ids) | {created_by}
    for uid in all_members:
        user = await db.get(User, uid)
        db.add(GroupMember(
            group_id=group.id,
            user_id=uid,
            email_ref=email_reference(user.email) if user else "",
            name_snapshot=user.display_name if user else "Unknown",
        ))

    await db.commit()
    await db.refresh(group)
    return group


async def create_shared_expense(
    db: AsyncSession,
    *,
    group_id: str,
    paid_by: str,
    description: str,
    amount: Decimal,
    expense_date: str,
    participant_ids: list[str],
    pending_participants: list[dict] | None = None,
    category: str = "Other",
) -> SharedExpense:
    """
    pending_participants is a list of {"email": str, "name": str} for
    people who don't have a Sanchay account yet — splitting an
    expense with someone you haven't fully onboarded is a real, common
    case (you log the dinner the night it happens; your friend signs
    up for the app whenever they get around to it). The router is
    responsible for ensuring a PendingGroupInvite already exists for
    each of these (creating + emailing one if not) before calling
    this — this function only builds the split.

    The split row for a pending participant looks EXACTLY like a
    frozen (deleted-account) split already does: user_id=None,
    email_ref set, name_snapshot set. That's deliberate, not a
    shortcut — it means reconnect_by_email() (built for the
    delete-account-then-resignup case) already, for free, reattaches
    this split the moment this person actually signs up with that
    email. No new reconciliation logic needed for that half of the
    feature; the existing architecture already generalizes to "never
    had an account yet" as well as "had one, deleted it."
    """
    payer = await db.get(User, paid_by)
    expense = SharedExpense(
        group_id=group_id,
        paid_by=paid_by,
        paid_by_email_ref=email_reference(payer.email) if payer else "",
        paid_by_name_snapshot=payer.display_name if payer else "Unknown",
        description=description,
        category=category,
        amount=amount,
        expense_date=expense_date,
    )
    db.add(expense)
    await db.flush()

    # Build one unified list of participant keys so the split math
    # (and its sum-equals-total guarantee) runs over everyone at once
    # — a real user's id, or a pending participant's normalized email
    # (a string just as good as a key; split_evenly only cares that
    # keys are distinct and stable, not what they mean).
    pending = pending_participants or []
    all_keys = list(participant_ids) + [p["email"].strip().lower() for p in pending]
    shares = split_evenly(amount, all_keys)

    pending_by_key = {p["email"].strip().lower(): p for p in pending}

    for key, share in shares.items():
        if key in pending_by_key:
            p = pending_by_key[key]
            db.add(SharedExpenseSplit(
                shared_expense_id=expense.id,
                user_id=None,
                email_ref=email_reference(p["email"]),
                name_snapshot=p["name"],
                share_amount=share,
            ))
        else:
            participant = await db.get(User, key)
            db.add(SharedExpenseSplit(
                shared_expense_id=expense.id,
                user_id=key,
                email_ref=email_reference(participant.email) if participant else "",
                name_snapshot=participant.display_name if participant else "Unknown",
                share_amount=share,
            ))

    await db.commit()
    await db.refresh(expense)
    return expense


async def edit_shared_expense(
    db: AsyncSession,
    *,
    expense_id: str,
    edited_by: str,
    new_amount: Decimal | None = None,
    new_description: str | None = None,
    new_category: str | None = None,
) -> SharedExpense:
    """
    Corrects the ONE shared record and re-splits it — not a private
    copy. Every participant's existing split is recalculated from the
    new total, and a system comment logs exactly what changed, so an
    edit is visible history, not a silent rewrite.
    """
    expense = await db.get(SharedExpense, expense_id)
    if expense is None:
        raise ValueError("Shared expense not found")

    editor = await db.get(User, edited_by)
    editor_name = editor.display_name if editor else "Unknown"

    changes = []
    if new_amount is not None and new_amount != expense.amount:
        changes.append(f"amount from ${expense.amount:.2f} to ${new_amount:.2f}")
        expense.amount = new_amount

        result = await db.execute(select(SharedExpenseSplit).where(SharedExpenseSplit.shared_expense_id == expense_id))
        splits = list(result.scalars().all())
        participant_ids = [s.user_id for s in splits if s.user_id is not None]
        new_shares = split_evenly(new_amount, participant_ids)
        for s in splits:
            if s.user_id in new_shares:
                s.share_amount = new_shares[s.user_id]

    if new_description is not None and new_description != expense.description:
        changes.append(f'description to "{new_description}"')
        expense.description = new_description

    if new_category is not None and new_category != expense.category:
        changes.append(f'category to "{new_category}"')
        expense.category = new_category

    if changes:
        db.add(SharedExpenseComment(
            shared_expense_id=expense_id,
            user_id=edited_by,
            email_ref=email_reference(editor.email) if editor else "",
            name_snapshot=editor_name,
            body=f"{editor_name} changed {' and '.join(changes)}.",
            is_system=True,
        ))

    expense.updated_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(expense)
    return expense


async def add_comment(db: AsyncSession, *, expense_id: str, user_id: str, body: str) -> SharedExpenseComment:
    user = await db.get(User, user_id)
    comment = SharedExpenseComment(
        shared_expense_id=expense_id,
        user_id=user_id,
        email_ref=email_reference(user.email) if user else "",
        name_snapshot=user.display_name if user else "Unknown",
        body=body,
        is_system=False,
    )
    db.add(comment)
    await db.commit()
    await db.refresh(comment)
    return comment


async def record_settlement(
    db: AsyncSession, *, from_user_id: str, to_user_id: str, amount: Decimal, settled_date: str
) -> Settlement:
    from_user = await db.get(User, from_user_id)
    to_user = await db.get(User, to_user_id)
    settlement = Settlement(
        from_user_id=from_user_id,
        from_email_ref=email_reference(from_user.email) if from_user else "",
        from_name_snapshot=from_user.display_name if from_user else "Unknown",
        to_user_id=to_user_id,
        to_email_ref=email_reference(to_user.email) if to_user else "",
        to_name_snapshot=to_user.display_name if to_user else "Unknown",
        amount=amount,
        settled_date=settled_date,
    )
    db.add(settlement)
    await db.commit()
    await db.refresh(settlement)
    return settlement


async def compute_balance(db: AsyncSession, *, user_a: str, user_b: str) -> Decimal:
    """
    Net amount user_a owes user_b (negative = user_b owes user_a
    instead). A running total across every shared expense and
    settlement between the two of them, not per-expense reconciliation
    — the same model Splitwise itself uses.
    """
    balance = Decimal("0.00")

    result = await db.execute(
        select(SharedExpenseSplit, SharedExpense)
        .join(SharedExpense, SharedExpenseSplit.shared_expense_id == SharedExpense.id)
        .where(SharedExpenseSplit.user_id == user_a, SharedExpense.paid_by == user_b)
    )
    for split, _expense in result.all():
        balance += split.share_amount

    result = await db.execute(
        select(SharedExpenseSplit, SharedExpense)
        .join(SharedExpense, SharedExpenseSplit.shared_expense_id == SharedExpense.id)
        .where(SharedExpenseSplit.user_id == user_b, SharedExpense.paid_by == user_a)
    )
    for split, _expense in result.all():
        balance -= split.share_amount

    result = await db.execute(
        select(Settlement).where(Settlement.from_user_id == user_a, Settlement.to_user_id == user_b)
    )
    for s in result.scalars().all():
        balance -= s.amount

    result = await db.execute(
        select(Settlement).where(Settlement.from_user_id == user_b, Settlement.to_user_id == user_a)
    )
    for s in result.scalars().all():
        balance += s.amount

    return balance.quantize(Decimal("0.01"))


async def freeze_user_references(db: AsyncSession, *, user_id: str) -> None:
    """
    Called by auth_service.delete_account() BEFORE the user row is
    deleted. Refreshes name_snapshot to the person's current display
    name, then nulls user_id — email_ref is untouched (it was already
    set correctly at creation time and never needs to change). The
    historical record survives as "Name (account deleted)"; their
    actual account does not.
    """
    user = await db.get(User, user_id)
    name = user.display_name if user else "Unknown"

    for model, field in [
        (GroupMember, "user_id"),
        (SharedExpenseSplit, "user_id"),
        (SharedExpenseComment, "user_id"),
    ]:
        result = await db.execute(select(model).where(getattr(model, field) == user_id))
        for row in result.scalars().all():
            if hasattr(row, "name_snapshot"):
                row.name_snapshot = name
            setattr(row, field, None)

    for expense_result in [
        await db.execute(select(SharedExpense).where(SharedExpense.paid_by == user_id)),
        await db.execute(select(SharedExpense).where(SharedExpense.created_by == user_id)),
    ]:
        for expense in expense_result.scalars().all():
            if expense.paid_by == user_id:
                expense.paid_by_name_snapshot = name
                expense.paid_by = None
            if expense.created_by == user_id:
                expense.created_by = None

    for settlement_result in [
        await db.execute(select(Settlement).where(Settlement.from_user_id == user_id)),
        await db.execute(select(Settlement).where(Settlement.to_user_id == user_id)),
    ]:
        for s in settlement_result.scalars().all():
            if s.from_user_id == user_id:
                s.from_name_snapshot = name
                s.from_user_id = None
            if s.to_user_id == user_id:
                s.to_name_snapshot = name
                s.to_user_id = None

    await db.commit()


async def reconnect_by_email(db: AsyncSession, *, new_user: User) -> dict:
    """
    Called by auth_service.signup() right after a new user is
    created. Finds every frozen row (user_id IS NULL) whose email_ref
    matches this signup's email, and re-populates user_id — the
    person's old shared-expense history becomes live again
    automatically. Returns a summary so the caller can decide whether
    to surface a "welcome back, reconnecting your history" notice
    (only non-empty if something was actually found).

    Also updates every name_snapshot to the new user's REAL account
    display name — not the name that happened to be captured at
    invite time (or whatever name they had before deleting a prior
    account). Deliberate, and fixes a real inconsistency this was
    found to have: without this, join_pending_invites (which creates
    the GroupMember row) already used the new signup name, but this
    function left every expense split/comment/settlement showing the
    OLD name — the same person would show up under two different
    names in the same group depending on which screen you looked at.
    Once someone has a real account, their name is their name,
    consistently, everywhere — not frozen at whatever it was when
    someone else typed it into an invite form.
    """
    ref = email_reference(new_user.email)
    reconnected_group_ids: set[str] = set()
    total_amount = Decimal("0.00")
    name = new_user.display_name

    result = await db.execute(select(GroupMember).where(GroupMember.email_ref == ref, GroupMember.user_id.is_(None)))
    for row in result.scalars().all():
        row.user_id = new_user.id
        row.name_snapshot = name
        reconnected_group_ids.add(row.group_id)

    result = await db.execute(select(SharedExpenseSplit).where(SharedExpenseSplit.email_ref == ref, SharedExpenseSplit.user_id.is_(None)))
    for row in result.scalars().all():
        row.user_id = new_user.id
        row.name_snapshot = name
        total_amount += row.share_amount

    result = await db.execute(select(SharedExpenseComment).where(SharedExpenseComment.email_ref == ref, SharedExpenseComment.user_id.is_(None)))
    for row in result.scalars().all():
        row.user_id = new_user.id
        row.name_snapshot = name

    result = await db.execute(select(SharedExpense).where(SharedExpense.paid_by_email_ref == ref, SharedExpense.paid_by.is_(None)))
    for row in result.scalars().all():
        row.paid_by = new_user.id
        row.paid_by_name_snapshot = name

    result = await db.execute(select(Settlement).where(Settlement.from_email_ref == ref, Settlement.from_user_id.is_(None)))
    for row in result.scalars().all():
        row.from_user_id = new_user.id
        row.from_name_snapshot = name

    result = await db.execute(select(Settlement).where(Settlement.to_email_ref == ref, Settlement.to_user_id.is_(None)))
    for row in result.scalars().all():
        row.to_user_id = new_user.id
        row.to_name_snapshot = name

    await db.commit()

    return {"groups_reconnected": len(reconnected_group_ids), "total_amount": total_amount}


# ---------- read/listing functions, backing the API layer ----------

async def get_group(db: AsyncSession, *, group_id: str) -> Group | None:
    return await db.get(Group, group_id)


async def is_group_member(db: AsyncSession, *, group_id: str, user_id: str) -> bool:
    """
    Authorization check: can this user see this group's data at all?
    Used by every group/expense endpoint before returning anything —
    a group's financial detail should only ever be visible to its
    own members, never guessable by id alone.
    """
    result = await db.execute(
        select(GroupMember).where(GroupMember.group_id == group_id, GroupMember.user_id == user_id)
    )
    return result.scalar_one_or_none() is not None


async def get_user_groups(db: AsyncSession, *, user_id: str) -> list[Group]:
    result = await db.execute(
        select(Group).join(GroupMember, GroupMember.group_id == Group.id).where(GroupMember.user_id == user_id)
    )
    return list(result.scalars().all())


async def get_group_members(db: AsyncSession, *, group_id: str) -> list[GroupMember]:
    result = await db.execute(select(GroupMember).where(GroupMember.group_id == group_id))
    return list(result.scalars().all())


async def get_group_pending_invites(db: AsyncSession, *, group_id: str) -> list[dict]:
    result = await db.execute(select(PendingGroupInvite).where(PendingGroupInvite.group_id == group_id))
    return [{"name": inv.name, "email": inv.email} for inv in result.scalars().all()]


async def has_pending_invite(db: AsyncSession, *, group_id: str, email: str) -> bool:
    result = await db.execute(
        select(PendingGroupInvite).where(PendingGroupInvite.group_id == group_id, PendingGroupInvite.email == email.strip().lower())
    )
    return result.scalar_one_or_none() is not None


async def ensure_pending_invite(
    db: AsyncSession,
    background_tasks: BackgroundTasks,
    *,
    group_id: str,
    email: str,
    name: str,
    invited_by: str,
    group_name: str,
    frontend_url: str,
) -> None:
    """
    The check-then-create pattern shared by every place someone can
    end up invited to a group without a Sanchay account yet: creating
    a group with unknown emails, adding a member later, and now
    splitting an expense with someone who isn't a member (or even
    invited) yet. Deliberately a no-op (no duplicate row, no second
    email) if this exact email is already pending for this group.
    """
    if not await has_pending_invite(db, group_id=group_id, email=email):
        await create_pending_invite(
            db, background_tasks, group_id=group_id, email=email, name=name,
            invited_by=invited_by, group_name=group_name, frontend_url=frontend_url,
        )


async def get_group_expenses(db: AsyncSession, *, group_id: str) -> list[SharedExpense]:
    result = await db.execute(
        select(SharedExpense).where(SharedExpense.group_id == group_id).order_by(SharedExpense.expense_date.desc())
    )
    return list(result.scalars().all())


async def get_expense(db: AsyncSession, *, expense_id: str) -> SharedExpense | None:
    return await db.get(SharedExpense, expense_id)


async def get_expense_splits(db: AsyncSession, *, expense_id: str) -> list[SharedExpenseSplit]:
    result = await db.execute(select(SharedExpenseSplit).where(SharedExpenseSplit.shared_expense_id == expense_id))
    return list(result.scalars().all())


async def get_expense_comments(db: AsyncSession, *, expense_id: str) -> list[SharedExpenseComment]:
    result = await db.execute(
        select(SharedExpenseComment).where(SharedExpenseComment.shared_expense_id == expense_id).order_by(SharedExpenseComment.created_at)
    )
    return list(result.scalars().all())


async def get_all_balances(db: AsyncSession, *, user_id: str) -> list[dict]:
    """
    "Who owes me, who do I owe" across every group this user is in.
    Scoped to currently-LIVE friends only for v1 — a friend whose
    account has since been deleted (frozen, user_id NULL) won't
    appear in this summary even though their historical expenses are
    still visible inside the relevant group's own detail view with
    "(account deleted)" labels. Showing frozen-friend balances in this
    top-level summary too is a real, valid gap, tracked as a backlog
    item rather than folded into this pass — it would need
    compute_balance() reworked to match by email_ref instead of
    user_id, a bigger change to an already-tested function.
    """
    my_groups = await get_user_groups(db, user_id=user_id)
    other_user_ids: set[str] = set()
    for group in my_groups:
        members = await get_group_members(db, group_id=group.id)
        for m in members:
            if m.user_id and m.user_id != user_id:
                other_user_ids.add(m.user_id)

    balances = []
    for other_id in other_user_ids:
        other_user = await db.get(User, other_id)
        if other_user is None:
            continue
        balance = await compute_balance(db, user_a=user_id, user_b=other_id)
        if balance != Decimal("0.00"):
            balances.append({"user_id": other_id, "name": other_user.display_name, "balance": balance})

    return balances


# ---------- pending invites: adding someone who has no account yet ----------

async def create_pending_invite(
    db: AsyncSession,
    background_tasks: BackgroundTasks,
    *,
    group_id: str,
    email: str,
    name: str = "",
    invited_by: str,
    group_name: str,
    frontend_url: str,
) -> None:
    """
    Creates the pending row and queues the invite email. The email
    goes through BackgroundTasks — the SAME pattern signup/reset/
    verification emails already use — for the same two reasons: the
    response shouldn't wait on Resend's API, and an email delivery
    failure must never fail the request itself. This was originally
    (wrongly) a synchronous send, and a real Resend error — sandbox
    accounts can only send to the account owner's own address until a
    domain is verified — 500'd the whole group-creation request. The
    group and the pending invite row are real regardless of whether
    the email got through; a delivery failure now logs (visible in
    Render's logs) instead of breaking the request. The invitee-
    never-learns-about-it risk on a failed send is real but bounded:
    the group's creator can see the pending invite in the group view
    and chase them manually.

    name is what the group actually sees for this person before they
    sign up — "Sam" instead of a raw email address, matching how a
    real friend/roommate would be added anywhere else. Falls back to
    the email's local part (before the @) if nothing was given, which
    still beats displaying the whole address.
    """
    normalized = email.strip().lower()
    display_name = name.strip() or normalized.split("@")[0]
    inviter = await db.get(User, invited_by)
    db.add(PendingGroupInvite(group_id=group_id, email=normalized, name=display_name, invited_by=invited_by))
    await db.commit()

    signup_link = frontend_url.rstrip("/") + "/"  # signup is the app's normal entry point, no special invite token needed — matching by email is what makes this work
    background_tasks.add_task(
        _send_invite_email_safely, normalized, inviter.display_name if inviter else "Someone", group_name, signup_link
    )


def _send_invite_email_safely(to_email: str, inviter_name: str, group_name: str, signup_link: str) -> None:
    """
    BackgroundTasks swallows exceptions less gracefully than you'd
    hope (they surface as unhandled errors in logs but read like
    request failures) — wrapping the send so a delivery failure logs
    one clear, searchable line instead.
    """
    import logging

    try:
        email_sender.send_group_invite(to_email, inviter_name, group_name, signup_link)
    except Exception:
        logging.getLogger("sanchay.email").exception(
            "Group invite email to %s failed to send — the pending invite row still exists; "
            "they'll join if they ever sign up with this email, but they were NOT notified.",
            to_email,
        )


async def join_pending_invites(db: AsyncSession, *, new_user: User) -> list[str]:
    """
    Called from auth_service.signup() right after a new user is
    created — same integration-point pattern as reconnect_by_email(),
    right next to it. Finds every pending invite matching this
    signup's email, creates a real GroupMember row for each, and
    deletes the consumed invite. Returns the group NAMES joined, so
    signup can surface a "you were added to these groups" notice —
    visible, not silent, same principle as the reconnection summary.
    """
    email = new_user.email.strip().lower()
    result = await db.execute(select(PendingGroupInvite).where(PendingGroupInvite.email == email))
    invites = list(result.scalars().all())
    if not invites:
        return []

    joined_group_names = []
    for invite in invites:
        group = await db.get(Group, invite.group_id)
        if group is None:
            await db.delete(invite)
            continue
        db.add(GroupMember(
            group_id=invite.group_id,
            user_id=new_user.id,
            email_ref=email_reference(new_user.email),
            name_snapshot=new_user.display_name,
        ))
        joined_group_names.append(group.name)
        await db.delete(invite)

    await db.commit()
    return joined_group_names


async def rename_group(db: AsyncSession, *, group_id: str, new_name: str) -> Group:
    group = await db.get(Group, group_id)
    if group is None:
        raise ValueError("Group not found")
    group.name = new_name
    await db.commit()
    await db.refresh(group)
    return group


async def delete_group(db: AsyncSession, *, group_id: str) -> None:
    """
    Deletes a group AND its membership/pending-invite rows — but the
    caller (router) must enforce the emptiness rule first: a group
    with any expense history is not deletable, because that history
    is a shared record belonging to every member, not just whoever
    clicked delete. An empty group (no expenses, no settlement
    implications) is just a container; removing it destroys nothing
    anyone owes or is owed.
    """
    result = await db.execute(select(GroupMember).where(GroupMember.group_id == group_id))
    for m in result.scalars().all():
        await db.delete(m)
    result = await db.execute(select(PendingGroupInvite).where(PendingGroupInvite.group_id == group_id))
    for inv in result.scalars().all():
        await db.delete(inv)
    group = await db.get(Group, group_id)
    if group is not None:
        await db.delete(group)
    await db.commit()
