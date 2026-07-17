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
from decimal import ROUND_FLOOR, Decimal

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
from app.models.shared_recurring_rule import SharedRecurringRule
from app.models.user import User
from app.repositories import user_repository
from app.services.recurring_date_math import due_occurrences


def email_reference(email: str) -> str:
    """
    SHA-256 of the normalized (lowercased, trimmed) email — never the
    raw address itself. Deterministic: the same email always produces
    the same reference, which is the whole mechanism reconnection
    relies on, without this module ever needing to store or expose
    anyone's actual email to other group members.
    """
    return hash_token(email.strip().lower())


def _split_by_weights(total: Decimal, weights: dict[str, Decimal]) -> dict[str, Decimal]:
    """
    The general case split_evenly, split_by_shares, and
    split_by_percentage all reduce to: distribute `total` among keys
    proportional to their weight, using the largest-remainder method
    so the parts always sum to `total` exactly regardless of rounding
    — the property that actually matters, since a split that doesn't
    sum to the total means the group balance can never truly reach
    zero even after everyone pays. Weights don't need to sum to
    anything in particular (shares and percentages both work — 2:1:1
    shares and 50:25:25 percentages produce identical splits); they
    only need to be non-negative and not all zero.
    """
    keys = list(weights.keys())
    total_weight = sum(weights.values())
    if not keys or total_weight <= 0:
        return {}

    cents_total = int((total * 100).to_integral_value())
    exact_cents = {k: (Decimal(cents_total) * weights[k] / total_weight) for k in keys}
    base_cents = {k: int(exact_cents[k].to_integral_value(rounding=ROUND_FLOOR)) for k in keys}
    remainder_cents = cents_total - sum(base_cents.values())

    remainders = sorted(keys, key=lambda k: exact_cents[k] - base_cents[k], reverse=True)
    shares_cents = dict(base_cents)
    for i in range(remainder_cents):
        shares_cents[remainders[i]] += 1

    return {k: (Decimal(c) / 100).quantize(Decimal("0.01")) for k, c in shares_cents.items()}


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


class SplitValidationError(ValueError):
    """Raised by split_by_percentage/split_exact when the given values don't add up — a real, expected user-facing error, not a bug."""


def split_by_shares(total: Decimal, shares: dict[str, Decimal]) -> dict[str, Decimal]:
    """
    Proportional split — "Alice gets 2 shares, Bob gets 1" means Alice
    owes twice what Bob does, regardless of how many people are
    involved or what the total is. Zero-or-negative total shares for
    everyone is rejected (nothing to distribute proportionally); an
    individual share of 0 is fine (that person owes nothing but stays
    listed, e.g. someone who didn't eat but is still on the bill).
    """
    if sum(shares.values()) <= 0:
        raise SplitValidationError("At least one person needs a positive share")
    return _split_by_weights(total, shares)


def split_by_percentage(total: Decimal, percentages: dict[str, Decimal]) -> dict[str, Decimal]:
    """
    Percentages must sum to exactly 100 — unlike shares (which are
    just relative weights and can be any positive numbers), a
    percentage split is explicitly claiming "this is the whole bill,
    divided this way," so 97% or 103% is almost certainly a mistake
    the person would want caught immediately, not silently
    renormalized to add up anyway.
    """
    total_pct = sum(percentages.values())
    if total_pct != Decimal("100"):
        raise SplitValidationError(f"Percentages must add up to 100, got {total_pct}")
    return _split_by_weights(total, percentages)


def split_exact(total: Decimal, amounts: dict[str, Decimal]) -> dict[str, Decimal]:
    """
    Each person's share is given directly, in dollars — the split
    math itself is a no-op (there's no rounding to do; the person
    already typed exact cent amounts). What this function actually
    does is enforce the one thing that has to be true regardless of
    split method: the parts must sum to the total exactly. A mismatch
    is surfaced immediately as a real, expected error — silently
    accepting amounts that don't add up would mean the group balance
    can never truly reach zero even after everyone pays, exactly the
    failure mode every other split method in this file exists to
    prevent.
    """
    given_total = sum(amounts.values())
    if given_total != total:
        raise SplitValidationError(f"Amounts must add up to the total (${total}), got ${given_total}")
    return dict(amounts)


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


async def get_invite_preview(db: AsyncSession, *, invite_id: str) -> dict | None:
    """
    Backs the public, unauthenticated GET /invites/{id} — the one
    deliberate exception to "every group route checks membership
    first" documented at the top of the router. Safe specifically
    because it returns only a group name and an inviter's display
    name, nothing financial and nothing that requires already being
    a member to see. invite_id (PendingGroupInvite.id) is a UUID —
    unguessable, so this doesn't enable scanning for real groups.
    Returns None for an invalid or already-claimed invite; the router
    turns that into a 404 either way, so a stale link and a
    never-existed one look identical to whoever's holding it.
    """
    invite = await db.get(PendingGroupInvite, invite_id)
    if invite is None:
        return None
    group = await db.get(Group, invite.group_id)
    if group is None:
        return None
    inviter_name = "Someone"
    if invite.invited_by:
        inviter = await user_repository.get_by_id(db, invite.invited_by)
        if inviter is not None:
            inviter_name = inviter.display_name
    return {"group_name": group.name, "inviter_name": inviter_name, "invitee_name": invite.name}


async def accept_invite_link(db: AsyncSession, *, invite_id: str, user: User) -> Group | None:
    """
    The authenticated half of the invite-link flow. Deliberately does
    NOT check that the accepting user's email matches the email the
    inviter originally typed — same trust model as a Slack/Discord
    invite link (possession of the unguessable link is the
    authorization, not an email match). This is a genuine, deliberate
    widening from the email-only join path (join_pending_invites),
    which only ever matches by exact email — and fixes a real gap
    that path has: a typo'd invite email, or an invitee who doesn't
    control that inbox, could otherwise never join at all.

    Consumes (deletes) the specific invite row regardless of whether
    the group had other pending invites too — this one link, once
    used, is done, matching how single-use invite links normally
    behave.
    """
    invite = await db.get(PendingGroupInvite, invite_id)
    if invite is None:
        return None
    group = await db.get(Group, invite.group_id)
    if group is None:
        await db.delete(invite)
        await db.commit()
        return None
    await add_member_to_group(db, group_id=invite.group_id, user_id=user.id)
    await db.delete(invite)
    await db.commit()
    return group


async def create_group(db: AsyncSession, *, name: str, created_by: str, member_ids: list[str]) -> Group:
    creator = await db.get(User, created_by)
    group = Group(name=name, created_by=created_by, created_by_name_snapshot=creator.display_name if creator else "Unknown")
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
    paid_by: str | None,
    description: str,
    amount: Decimal,
    expense_date: str,
    participant_ids: list[str],
    pending_participants: list[dict] | None = None,
    category: str = "Other",
    split_type: str = "equal",
    participant_values: dict[str, Decimal] | None = None,
    paid_by_pending: dict | None = None,
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

    paid_by_pending (same {"email", "name"} shape) is the identical
    idea applied to who PAID rather than who's splitting it — exactly
    one of paid_by/paid_by_pending should be set by the time this is
    called (the router enforces that via the schema validator; this
    function just trusts it). reconnect_by_email() already has a
    branch for SharedExpense rows with paid_by IS NULL — it was
    written correctly for this case from early on, just never
    reachable until this parameter existed to produce such a row.
    """
    if paid_by_pending:
        payer_email_ref = email_reference(paid_by_pending["email"])
        payer_name = paid_by_pending["name"]
        resolved_paid_by = None
    else:
        payer = await db.get(User, paid_by)
        payer_email_ref = email_reference(payer.email) if payer else ""
        payer_name = payer.display_name if payer else "Unknown"
        resolved_paid_by = paid_by

    expense = SharedExpense(
        group_id=group_id,
        paid_by=resolved_paid_by,
        paid_by_email_ref=payer_email_ref,
        paid_by_name_snapshot=payer_name,
        description=description,
        category=category,
        split_type=split_type,
        amount=amount,
        expense_date=expense_date,
    )
    db.add(expense)
    await db.flush()

    await _write_splits(
        db, shared_expense_id=expense.id, amount=amount, participant_ids=participant_ids, pending_participants=pending_participants,
        split_type=split_type, values=participant_values,
    )

    await db.commit()
    await db.refresh(expense)
    return expense


async def _write_splits(
    db: AsyncSession,
    *,
    shared_expense_id: str,
    amount: Decimal,
    participant_ids: list[str],
    pending_participants: list[dict] | None,
    split_type: str = "equal",
    values: dict[str, Decimal] | None = None,
) -> None:
    """
    Shared by create_shared_expense and edit_shared_expense (when
    either the amount or who's included changes) — deletes any
    existing splits for this expense and writes fresh ones for the
    given participant set, using ONE unified list of keys so the
    split math (and its sum-equals-total guarantee) runs over
    everyone at once — a real user's id, or a pending participant's
    normalized email/email_ref (a string just as good as a key;
    the splitting functions only care that keys are distinct and
    stable, not what they mean).

    split_type dispatches to the right math (split_evenly /
    split_by_shares / split_by_percentage / split_exact) — values is
    only consulted for the non-"equal" cases, keyed the same way as
    all_keys (real participant_id, or a pending participant's
    normalized email). A key missing from values is treated as
    weight/amount 0 for shares/exact (present but contributing
    nothing — e.g. someone added to the split with no value entered
    yet); split_by_percentage and split_exact will then correctly
    reject the request if that leaves the total short, via their own
    validation.

    Each pending_participants dict is either {"email": str, "name":
    str} — a genuinely NEW pending participant, whose email needs
    hashing — or {"email_ref": str, "name": str} — a participant being
    RECONSTRUCTED from an existing split (e.g. re-splitting after an
    amount-only edit), whose email_ref is already the hash and must
    be used as-is. Getting this distinction wrong would silently
    double-hash an already-hashed value, producing a DIFFERENT
    email_ref than the person's real one — breaking
    reconnect_by_email()'s ability to ever find and reattach their
    split when they actually sign up. This was caught and fixed while
    building the ability to edit an expense's participants, before it
    shipped: the first draft always called email_reference(p["email"])
    unconditionally, which is only correct for genuinely new
    participants.

    Extracting this as its own function also fixed a real,
    pre-existing bug: the old inline re-split logic in
    edit_shared_expense filtered to `user_id is not None` before
    re-splitting, which silently EXCLUDED pending participants from
    ever having their share recalculated when the amount changed —
    their share_amount just stayed frozen at the old value, breaking
    the sum-equals-total guarantee the moment a pending participant
    was involved.
    """
    result = await db.execute(select(SharedExpenseSplit).where(SharedExpenseSplit.shared_expense_id == shared_expense_id))
    for row in result.scalars().all():
        await db.delete(row)
    await db.flush()

    pending = pending_participants or []

    def pending_key(p: dict) -> str:
        return p.get("email_ref") or p["email"].strip().lower()

    all_keys = list(participant_ids) + [pending_key(p) for p in pending]
    pending_by_key = {pending_key(p): p for p in pending}

    if split_type == "equal":
        shares = split_evenly(amount, all_keys)
    else:
        vals = values or {}
        weights_or_amounts = {k: Decimal(str(vals.get(k, 0))) for k in all_keys}
        if split_type == "shares":
            shares = split_by_shares(amount, weights_or_amounts)
        elif split_type == "percentage":
            shares = split_by_percentage(amount, weights_or_amounts)
        elif split_type == "exact":
            shares = split_exact(amount, weights_or_amounts)
        else:
            raise ValueError(f"Unknown split_type: {split_type}")

    for key, share in shares.items():
        if key in pending_by_key:
            p = pending_by_key[key]
            ref = p.get("email_ref") or email_reference(p["email"])
            db.add(SharedExpenseSplit(
                shared_expense_id=shared_expense_id,
                user_id=None,
                email_ref=ref,
                name_snapshot=p["name"],
                share_amount=share,
            ))
        else:
            participant = await db.get(User, key)
            db.add(SharedExpenseSplit(
                shared_expense_id=shared_expense_id,
                user_id=key,
                email_ref=email_reference(participant.email) if participant else "",
                name_snapshot=participant.display_name if participant else "Unknown",
                share_amount=share,
            ))


async def edit_shared_expense(
    db: AsyncSession,
    *,
    expense_id: str,
    edited_by: str,
    new_amount: Decimal | None = None,
    new_description: str | None = None,
    new_category: str | None = None,
    new_expense_date: str | None = None,
    new_participant_ids: list[str] | None = None,
    new_pending_participants: list[dict] | None = None,
    new_split_type: str | None = None,
    new_participant_values: dict[str, Decimal] | None = None,
    new_paid_by: str | None = None,
    new_paid_by_pending: dict | None = None,
) -> SharedExpense:
    """
    Corrects the ONE shared record and re-splits it — not a private
    copy. A system comment logs exactly what changed, so an edit is
    visible history, not a silent rewrite.

    new_participant_ids/new_pending_participants being non-None means
    "replace who's included" — not just recalculate the same
    people's shares. Passing an empty list is a real, meaningful
    request (remove everyone from that side), which is why the check
    is `is not None`, not truthiness. When only the amount changes
    (participants untouched), the CURRENT participant set is
    reconstructed from the existing splits and re-split via the same
    path — this also fixes a real bug that existed before this
    function supported changing participants at all: the old inline
    re-split logic filtered to `user_id is not None`, which silently
    excluded pending participants from ever getting their share
    recalculated when the amount changed, breaking the sum-equals-
    total guarantee. _write_splits doesn't have that bug.

    new_paid_by/new_paid_by_pending: unlike participants, there's no
    sensible "both None means clear it" — an expense always needs a
    payer. Both None means "leave paid_by exactly as it is"; providing
    either one changes it, same mutual exclusivity the schema already
    enforces for create. Editing the payer never touches the split
    amounts themselves (correcting WHO paid isn't the same claim as
    correcting HOW MUCH or WHO owes what).
    """
    expense = await db.get(SharedExpense, expense_id)
    if expense is None:
        raise ValueError("Shared expense not found")

    editor = await db.get(User, edited_by)
    editor_name = editor.display_name if editor else "Unknown"

    changes = []
    participants_changing = new_participant_ids is not None or new_pending_participants is not None
    amount_changing = new_amount is not None and new_amount != expense.amount
    split_config_changing = new_split_type is not None or new_participant_values is not None
    paid_by_changing = new_paid_by is not None or new_paid_by_pending is not None

    if paid_by_changing:
        old_payer_name = expense.paid_by_name_snapshot
        if new_paid_by_pending:
            expense.paid_by = None
            expense.paid_by_email_ref = email_reference(new_paid_by_pending["email"])
            expense.paid_by_name_snapshot = new_paid_by_pending["name"]
        else:
            payer = await db.get(User, new_paid_by)
            expense.paid_by = new_paid_by
            expense.paid_by_email_ref = email_reference(payer.email) if payer else ""
            expense.paid_by_name_snapshot = payer.display_name if payer else "Unknown"
        if expense.paid_by_name_snapshot != old_payer_name:
            changes.append(f"payer from {old_payer_name} to {expense.paid_by_name_snapshot}")

    if amount_changing:
        changes.append(f"amount from ${expense.amount:.2f} to ${new_amount:.2f}")
        expense.amount = new_amount

    if participants_changing:
        changes.append("who's splitting this")

    if split_config_changing and not participants_changing:
        changes.append("how it's split")

    if amount_changing or participants_changing or split_config_changing:
        if participants_changing:
            participant_ids = new_participant_ids if new_participant_ids is not None else []
            pending_participants = new_pending_participants if new_pending_participants is not None else []
        else:
            # Amount and/or split config changed but the participant set
            # didn't — reconstruct the CURRENT set (real + pending) from
            # the existing splits. Pending participants only ever have
            # their HASHED email_ref stored, never the raw address —
            # passed through as-is via the "email_ref" key so
            # _write_splits doesn't re-hash it (see _write_splits' own
            # docstring for why that matters).
            result = await db.execute(select(SharedExpenseSplit).where(SharedExpenseSplit.shared_expense_id == expense_id))
            existing_splits = list(result.scalars().all())
            participant_ids = [s.user_id for s in existing_splits if s.user_id is not None]
            pending_participants = [
                {"email_ref": s.email_ref, "name": s.name_snapshot} for s in existing_splits if s.user_id is None
            ]

        # The PERSISTED split_type is the correct fallback here, not a
        # hardcoded "equal" — someone adjusting only the VALUES of an
        # existing percentage split (without re-sending split_type,
        # since it didn't change) must still re-split as a percentage
        # split, not silently revert to equal.
        effective_split_type = new_split_type if new_split_type is not None else expense.split_type
        expense.split_type = effective_split_type

        await _write_splits(
            db, shared_expense_id=expense_id, amount=expense.amount,
            participant_ids=participant_ids, pending_participants=pending_participants,
            split_type=effective_split_type, values=new_participant_values,
        )

    if new_description is not None and new_description != expense.description:
        changes.append(f'description to "{new_description}"')
        expense.description = new_description

    if new_category is not None and new_category != expense.category:
        changes.append(f'category to "{new_category}"')
        expense.category = new_category

    if new_expense_date is not None and new_expense_date != expense.expense_date:
        changes.append(f"date to {new_expense_date}")
        expense.expense_date = new_expense_date

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
    db: AsyncSession, *,
    from_user_id: str | None = None, from_email_ref: str | None = None, from_name: str | None = None,
    to_user_id: str | None = None, to_email_ref: str | None = None, to_name: str | None = None,
    amount: Decimal, settled_date: str, group_id: str | None = None,
) -> Settlement:
    """
    Records a real payment. Either side can be a real, signed-up user
    (from_user_id / to_user_id) or someone identified only by
    email_ref + a name (from_email_ref+from_name / to_email_ref+to_name)
    -- pending (never signed up) and frozen (deleted account) are
    treated identically here, both are just "no real user_id yet."
    Exactly one form is expected per side; the router enforces which
    combinations are actually allowed (see SettlementCreateRequest's
    docstring for why "they paid me" is restricted to a pending/frozen
    counterparty), this function just trusts what it's given.

    group_id is optional and purely about where this shows up as an
    activity item (see Settlement's docstring) -- it does not change
    how the balance between the two people is computed; that's always
    a cross-group running net regardless of group_id.

    A pending/frozen party's row is created with user_id=NULL.
    reconnect_by_email() already re-attaches it (populates user_id,
    refreshes name_snapshot to their real account name) the moment
    that email signs up -- same integration point that already does
    this for SharedExpense/SharedExpenseSplit/GroupMember rows, so a
    settlement recorded today correctly carries forward once the
    person on the other end actually joins.
    """
    if from_user_id:
        from_user = await db.get(User, from_user_id)
        resolved_from_email_ref = email_reference(from_user.email) if from_user else ""
        resolved_from_name = from_user.display_name if from_user else "Unknown"
    else:
        resolved_from_email_ref = from_email_ref or ""
        resolved_from_name = from_name or "Unknown"

    if to_user_id:
        to_user = await db.get(User, to_user_id)
        resolved_to_email_ref = email_reference(to_user.email) if to_user else ""
        resolved_to_name = to_user.display_name if to_user else "Unknown"
    else:
        resolved_to_email_ref = to_email_ref or ""
        resolved_to_name = to_name or "Unknown"

    settlement = Settlement(
        group_id=group_id,
        from_user_id=from_user_id,
        from_email_ref=resolved_from_email_ref,
        from_name_snapshot=resolved_from_name,
        to_user_id=to_user_id,
        to_email_ref=resolved_to_email_ref,
        to_name_snapshot=resolved_to_name,
        amount=amount,
        settled_date=settled_date,
    )
    db.add(settlement)
    await db.commit()
    await db.refresh(settlement)
    return settlement


async def find_pending_or_frozen_name(db: AsyncSession, *, user_id: str, email_ref: str, group_id: str | None = None) -> str | None:
    """
    Looks up the display name for a specific pending/frozen
    email_ref, but ONLY if it's someone the caller actually shares a
    group with -- scans the caller's own groups' split/expense rows
    for a user_id-IS-NULL match, same data get_all_balances already
    builds frozen_friends/pending_friends from, just narrowed to one
    specific email_ref instead of collecting all of them.

    If group_id is given, the search is narrowed to just that one
    group -- used to validate a group-scoped settlement actually
    involves someone connected to THAT group specifically, not merely
    some other group the caller happens to also share with them.

    This is what stops record_settlement's pending-target path from
    being a way to fabricate a settlement against an arbitrary email
    address you found somewhere -- a client can't just supply any
    email_ref and a name; the name is only ever derived from a
    match this user has real, verifiable shared-expense history with.
    Returns None if no match, which the caller should treat as "reject
    the request," not "use a fallback name."
    """
    if group_id:
        my_groups = [g for g in await get_user_groups(db, user_id=user_id) if g.id == group_id]
    else:
        my_groups = await get_user_groups(db, user_id=user_id)
    for group in my_groups:
        expenses = await get_group_expenses(db, group_id=group.id)
        for expense in expenses:
            if expense.paid_by is None and expense.paid_by_email_ref == email_ref:
                return expense.paid_by_name_snapshot
            splits_result = await db.execute(select(SharedExpenseSplit).where(SharedExpenseSplit.shared_expense_id == expense.id))
            for split in splits_result.scalars().all():
                if split.user_id is None and split.email_ref == email_ref:
                    return split.name_snapshot
    return None


async def get_settlements_received(db: AsyncSession, *, user_id: str) -> list[Settlement]:
    """
    Every settlement where this user is the RECIPIENT (to_user_id),
    regardless of who recorded it -- which, per record_settlement's
    own one-sided design, is always the payer, on their own device.
    Sanchay is local-first and encrypted; there's no mechanism for
    Bob recording "I paid Alice back" to push a transaction into
    Alice's own local ledger. This is the read side that lets Alice's
    app notice a settlement exists that her local ledger doesn't know
    about yet, so it can prompt her to record where the money actually
    landed -- the receiving-side mirror of syncSharedExpenseToLedger's
    per-expense tracking, applied to settlements instead.
    """
    result = await db.execute(select(Settlement).where(Settlement.to_user_id == user_id).order_by(Settlement.settled_date))
    return list(result.scalars().all())


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


async def get_balance_breakdown(db: AsyncSession, *, user_id: str, other_user_id: str) -> list[dict]:
    """
    The actual list of expenses and settlements that add up to the net
    number compute_balance() returns for these two people -- "why do I
    owe $47.32," not just the total. Sorted oldest-first so it reads
    as a real running history, the same reasoning a receipt gives you.

    Scoped to live friends only (both are real user_ids) -- a frozen
    friend has no live other_user_id to look this up by in the first
    place, since get_all_balances() already returns user_id: None for
    them; there's nothing for the frontend to call this endpoint with.
    A frozen-friend breakdown is a real, disclosed gap left for later,
    not solved here.
    """
    items: list[dict] = []

    result = await db.execute(
        select(SharedExpenseSplit, SharedExpense)
        .join(SharedExpense, SharedExpenseSplit.shared_expense_id == SharedExpense.id)
        .where(SharedExpenseSplit.user_id == user_id, SharedExpense.paid_by == other_user_id)
    )
    for split, expense in result.all():
        group = await db.get(Group, expense.group_id)
        items.append({
            "type": "expense", "date": expense.expense_date, "group_name": group.name if group else "",
            "description": expense.description, "amount": split.share_amount,
            "direction": "you_owe",  # they paid, this is your share
        })

    result = await db.execute(
        select(SharedExpenseSplit, SharedExpense)
        .join(SharedExpense, SharedExpenseSplit.shared_expense_id == SharedExpense.id)
        .where(SharedExpenseSplit.user_id == other_user_id, SharedExpense.paid_by == user_id)
    )
    for split, expense in result.all():
        group = await db.get(Group, expense.group_id)
        items.append({
            "type": "expense", "date": expense.expense_date, "group_name": group.name if group else "",
            "description": expense.description, "amount": split.share_amount,
            "direction": "owed_to_you",  # you paid, this is their share
        })

    result = await db.execute(select(Settlement).where(Settlement.from_user_id == user_id, Settlement.to_user_id == other_user_id))
    for s in result.scalars().all():
        items.append({"type": "settlement", "date": s.settled_date, "group_name": None, "description": None, "amount": s.amount, "direction": "you_paid"})

    result = await db.execute(select(Settlement).where(Settlement.from_user_id == other_user_id, Settlement.to_user_id == user_id))
    for s in result.scalars().all():
        items.append({"type": "settlement", "date": s.settled_date, "group_name": None, "description": None, "amount": s.amount, "direction": "they_paid"})

    items.sort(key=lambda i: i["date"])
    return items


async def compute_group_debt_simplification(db: AsyncSession, *, group_id: str) -> list[dict]:
    """
    Minimizes the number of transactions needed to settle everyone's
    net position in this group, via the standard greedy algorithm
    (repeatedly match the largest creditor with the largest debtor) --
    the same approach Splitwise itself is known for. A real 3+ person
    cycle (A owes B, B owes C, C owes A the same amount) nets to zero
    total transfers needed; pairwise balances alone can't see that,
    since each pair looks non-zero in isolation.

    Deliberately scoped to this group's own expense splits only, NOT
    settlements -- Settlement is deliberately cross-group by design
    (see Settlement's own docstring: two people's balance is a running
    net across EVERYTHING they share, not tied to any one group), so a
    past settlement can't be attributed to "this group's" debts
    specifically. That means these suggested transfers are what this
    group's expenses alone would require, not a strict reconciliation
    against money that may have already changed hands for a different
    group entirely. A disclosed scoping choice forced by the data
    model, not an oversight -- the group detail view should say as
    much wherever this is surfaced.

    Frozen (deleted-account) participants are included, keyed by
    email_ref instead of user_id, same identity model used everywhere
    else for them -- someone owing money doesn't stop being real just
    because they deleted their account.
    """
    expenses = await get_group_expenses(db, group_id=group_id)
    net: dict[str, Decimal] = {}
    names: dict[str, str] = {}

    for expense in expenses:
        payer_key = expense.paid_by or f"frozen:{expense.paid_by_email_ref}"
        names[payer_key] = expense.paid_by_name_snapshot
        net[payer_key] = net.get(payer_key, Decimal("0.00")) + expense.amount

        splits_result = await db.execute(select(SharedExpenseSplit).where(SharedExpenseSplit.shared_expense_id == expense.id))
        for split in splits_result.scalars().all():
            split_key = split.user_id or f"frozen:{split.email_ref}"
            names[split_key] = split.name_snapshot
            net[split_key] = net.get(split_key, Decimal("0.00")) - split.share_amount

    creditors = sorted(((k, v) for k, v in net.items() if v > Decimal("0.005")), key=lambda x: -x[1])
    debtors = sorted(((k, v) for k, v in net.items() if v < Decimal("-0.005")), key=lambda x: x[1])

    transfers: list[dict] = []
    ci, di = 0, 0
    while ci < len(creditors) and di < len(debtors):
        cred_key, cred_amt = creditors[ci]
        debt_key, debt_amt = debtors[di]
        amount = min(cred_amt, -debt_amt).quantize(Decimal("0.01"))
        if amount > Decimal("0.00"):
            transfers.append({
                "from_key": debt_key, "from_name": names[debt_key],
                "to_key": cred_key, "to_name": names[cred_key],
                "amount": amount,
            })
        creditors[ci] = (cred_key, cred_amt - amount)
        debtors[di] = (debt_key, debt_amt + amount)
        if creditors[ci][1] <= Decimal("0.005"):
            ci += 1
        if debtors[di][1] >= Decimal("-0.005"):
            di += 1

    return transfers


async def compute_balance_with_frozen_friend(db: AsyncSession, *, user_id: str, friend_email_ref: str) -> Decimal:
    """
    Same running-total model as compute_balance(), for a friend who
    either never signed up (pending) or whose account has since been
    deleted (frozen) -- either way, user_id is NULL on their own
    rows and email_ref is what identifies them.

    Settlements ARE included here now -- record_settlement supports
    targeting a pending/frozen person by email_ref (no real user_id
    required to record a real payment), and reconnect_by_email()
    already re-attaches those rows' user_id once that email signs up,
    so this was a real gap to close, not settle for: without this, a
    settlement recorded against a pending friend would never actually
    move their balance, making the whole feature pointless.
    """
    balance = Decimal("0.00")

    result = await db.execute(
        select(SharedExpenseSplit, SharedExpense)
        .join(SharedExpense, SharedExpenseSplit.shared_expense_id == SharedExpense.id)
        .where(SharedExpenseSplit.user_id == user_id, SharedExpense.paid_by.is_(None), SharedExpense.paid_by_email_ref == friend_email_ref)
    )
    for split, _expense in result.all():
        balance += split.share_amount

    result = await db.execute(
        select(SharedExpenseSplit, SharedExpense)
        .join(SharedExpense, SharedExpenseSplit.shared_expense_id == SharedExpense.id)
        .where(SharedExpenseSplit.user_id.is_(None), SharedExpenseSplit.email_ref == friend_email_ref, SharedExpense.paid_by == user_id)
    )
    for split, _expense in result.all():
        balance -= split.share_amount

    result = await db.execute(
        select(Settlement).where(Settlement.from_user_id == user_id, Settlement.to_email_ref == friend_email_ref)
    )
    for s in result.scalars().all():
        balance -= s.amount

    result = await db.execute(
        select(Settlement).where(Settlement.from_email_ref == friend_email_ref, Settlement.to_user_id == user_id)
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

    result = await db.execute(select(Group).where(Group.created_by == user_id))
    for group in result.scalars().all():
        group.created_by_name_snapshot = name
        group.created_by = None

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

    result = await db.execute(select(SharedRecurringRule).where(SharedRecurringRule.created_by == user_id))
    for rule in result.scalars().all():
        rule.created_by_name_snapshot = name
        rule.created_by = None

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

    result = await db.execute(select(SharedRecurringRule).where(SharedRecurringRule.created_by_email_ref == ref, SharedRecurringRule.created_by.is_(None)))
    for row in result.scalars().all():
        row.created_by = new_user.id
        row.created_by_name_snapshot = name

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
    return [{"id": inv.id, "name": inv.name, "email": inv.email} for inv in result.scalars().all()]


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


async def get_group_settlements(db: AsyncSession, *, group_id: str) -> list[Settlement]:
    result = await db.execute(
        select(Settlement).where(Settlement.group_id == group_id).order_by(Settlement.settled_date.desc())
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


async def delete_shared_expense(db: AsyncSession, *, expense_id: str) -> None:
    """
    Unlike deleting a GROUP or removing a MEMBER, an individual
    expense has no "protect the history" constraint of its own to
    check first — an expense IS the history; there's nothing beneath
    it that deleting it would orphan. Any group member can delete an
    expense (a real, common need: duplicate entry, wrong group,
    logged by mistake) — deletes the expense along with its splits
    and comment thread.
    """
    result = await db.execute(select(SharedExpenseSplit).where(SharedExpenseSplit.shared_expense_id == expense_id))
    for row in result.scalars().all():
        await db.delete(row)
    result = await db.execute(select(SharedExpenseComment).where(SharedExpenseComment.shared_expense_id == expense_id))
    for row in result.scalars().all():
        await db.delete(row)
    expense = await db.get(SharedExpense, expense_id)
    if expense is not None:
        await db.delete(expense)
    await db.commit()


async def get_all_balances(db: AsyncSession, *, user_id: str) -> list[dict]:
    """
    "Who owes me, who do I owe" across every group this user is in.
    Includes live friends (matched by user_id), frozen friends --
    someone whose account was deleted, matched by email_ref instead
    (see compute_balance_with_frozen_friend's docstring for the one
    real gap that comes with that: their pre-deletion settlements
    aren't reflected, since Settlement rows are never frozen, only
    split/expense rows are) -- AND pending friends, someone who's
    never signed up at all but still has a real balance worth
    tracking (the whole point of splitting an expense with someone
    you haven't onboarded yet). This used to only cover live friends,
    a documented gap -- balances for the other two categories were
    invisible here even though their historical expenses stayed
    visible inside the group's own detail view.

    BUG FIXED HERE, found via a real report: user_id IS NULL on a
    split/expense row means one of TWO different things (see
    create_shared_expense's docstring) -- "this person's account was
    deleted" (genuinely frozen) OR "this person never signed up in
    the first place" (still just a pending invite, PendingGroupInvite
    row still exists for them). The first pass at frozen-friend
    support treated BOTH as "(account deleted)," which is wrong and
    needlessly alarming for the much more common pending case. The
    NEXT pass (still within this same fix) correctly stopped
    mislabeling pending participants as frozen, but as a side effect
    dropped them from this list ENTIRELY -- they matched neither the
    live-user branch nor the (now correctly narrowed) frozen branch,
    so a real, trackable balance with someone silently vanished from
    the summary. Fixed properly by tracking pending and frozen as two
    separate dicts, keyed the same way (email_ref -> name), and
    including both in the final balances list -- only frozen ones get
    is_frozen: True.
    """
    my_groups = await get_user_groups(db, user_id=user_id)
    other_user_ids: set[str] = set()
    frozen_friends: dict[str, str] = {}  # email_ref -> name_snapshot
    pending_friends: dict[str, str] = {}  # email_ref -> name_snapshot
    for group in my_groups:
        members = await get_group_members(db, group_id=group.id)
        for m in members:
            if m.user_id and m.user_id != user_id:
                other_user_ids.add(m.user_id)

        pending = await get_group_pending_invites(db, group_id=group.id)
        pending_email_refs = {email_reference(inv["email"]) for inv in pending}

        expenses = await get_group_expenses(db, group_id=group.id)
        for expense in expenses:
            if expense.paid_by is None:
                target = pending_friends if expense.paid_by_email_ref in pending_email_refs else frozen_friends
                target[expense.paid_by_email_ref] = expense.paid_by_name_snapshot
            splits_result = await db.execute(select(SharedExpenseSplit).where(SharedExpenseSplit.shared_expense_id == expense.id))
            for split in splits_result.scalars().all():
                if split.user_id is None:
                    target = pending_friends if split.email_ref in pending_email_refs else frozen_friends
                    target[split.email_ref] = split.name_snapshot

    balances = []
    for other_id in other_user_ids:
        other_user = await db.get(User, other_id)
        if other_user is None:
            continue
        balance = await compute_balance(db, user_a=user_id, user_b=other_id)
        if balance != Decimal("0.00"):
            balances.append({"user_id": other_id, "email_ref": None, "name": other_user.display_name, "balance": balance, "is_frozen": False})

    for email_ref, name in pending_friends.items():
        balance = await compute_balance_with_frozen_friend(db, user_id=user_id, friend_email_ref=email_ref)
        if balance != Decimal("0.00"):
            balances.append({"user_id": None, "email_ref": email_ref, "name": name, "balance": balance, "is_frozen": False})

    for email_ref, name in frozen_friends.items():
        balance = await compute_balance_with_frozen_friend(db, user_id=user_id, friend_email_ref=email_ref)
        if balance != Decimal("0.00"):
            balances.append({"user_id": None, "email_ref": email_ref, "name": name, "balance": balance, "is_frozen": True})

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


# ---------- recurring shared expenses ----------

VALID_FREQUENCIES = {"weekly", "biweekly", "monthly", "quarterly", "yearly"}


async def create_recurring_rule(
    db: AsyncSession,
    *,
    group_id: str,
    created_by: str | None,
    description: str,
    amount: Decimal,
    category: str,
    split_type: str,
    participant_ids: list[str],
    pending_participants: list[dict] | None,
    participant_values: dict[str, float] | None,
    frequency: str,
    start_date: str,
    end_date: str | None,
    created_by_pending: dict | None = None,
) -> SharedRecurringRule:
    """
    Creates the SCHEDULE only — no SharedExpense rows exist yet from
    this call. materialize_due_shared_expenses() is what turns due
    occurrences into real expenses, called lazily whenever a group's
    expenses/balances are actually read (see that function's own
    docstring for why lazy catch-up, not a background job).

    created_by/created_by_pending: same idea and same mutual
    exclusivity as create_shared_expense's paid_by/paid_by_pending —
    who pays each materialized occurrence, defaulting to the caller
    when both are omitted. Reuses the SAME created_by/
    created_by_name_snapshot columns the rule already had rather than
    adding new ones: those fields were always effectively "who pays"
    in practice (materialize_due_shared_expenses already used
    created_by as the payer for every occurrence) — this just makes
    that overridable instead of hardcoded to whoever set the rule up.
    """
    if frequency not in VALID_FREQUENCIES:
        raise ValueError(f"Unknown frequency: {frequency}")

    if created_by_pending:
        payer_email_ref = email_reference(created_by_pending["email"])
        payer_name = created_by_pending["name"]
        resolved_created_by = None
    else:
        creator = await db.get(User, created_by)
        payer_email_ref = email_reference(creator.email) if creator else ""
        payer_name = creator.display_name if creator else "Unknown"
        resolved_created_by = created_by

    rule = SharedRecurringRule(
        group_id=group_id,
        created_by=resolved_created_by,
        created_by_name_snapshot=payer_name,
        created_by_email_ref=payer_email_ref,
        description=description,
        amount=amount,
        category=category,
        split_type=split_type,
        participant_ids=participant_ids,
        pending_participants=pending_participants or [],
        participant_values={k: str(v) for k, v in (participant_values or {}).items()},
        frequency=frequency,
        start_date=start_date,
        end_date=end_date,
    )
    db.add(rule)
    await db.commit()
    await db.refresh(rule)
    return rule


async def edit_recurring_rule(
    db: AsyncSession,
    *,
    rule_id: str,
    new_description: str | None = None,
    new_amount: Decimal | None = None,
    new_category: str | None = None,
    new_split_type: str | None = None,
    new_participant_ids: list[str] | None = None,
    new_pending_participants: list[dict] | None = None,
    new_participant_values: dict[str, Decimal] | None = None,
    new_frequency: str | None = None,
    new_end_date: str | None = None,
    clear_end_date: bool = False,
    new_created_by: str | None = None,
    new_created_by_pending: dict | None = None,
) -> SharedRecurringRule | None:
    """
    Edits the SCHEDULE going forward — never touches start_date or
    last_materialized, and never reaches back to modify any expense
    already materialized from this rule (those are independent
    records with their own edit path, same "corrections don't rewrite
    history" principle edit_shared_expense already follows). A rule
    with occurrences already generated keeps generating on the SAME
    cadence/anchor after this; only what a FUTURE occurrence looks
    like (amount, who's splitting it, category, etc.) changes.

    Every new_* parameter follows the same "None means leave it
    alone" convention as edit_shared_expense — including
    new_created_by/new_created_by_pending (both None means don't
    touch who pays, matching that a schedule always needs SOME payer,
    there's no meaningful "clear it" the way there is for end_date).
    end_date is the one field that DOES need an explicit clear
    signal (clear_end_date=True) since None is genuinely ambiguous
    for it — a rule that runs forever is a real, different thing from
    "don't change whatever end date it already has."
    """
    rule = await db.get(SharedRecurringRule, rule_id)
    if rule is None:
        return None

    if new_frequency is not None and new_frequency not in VALID_FREQUENCIES:
        raise ValueError(f"Unknown frequency: {new_frequency}")

    if new_description is not None:
        rule.description = new_description
    if new_amount is not None:
        rule.amount = new_amount
    if new_category is not None:
        rule.category = new_category
    if new_split_type is not None:
        rule.split_type = new_split_type
    if new_participant_ids is not None:
        rule.participant_ids = new_participant_ids
    if new_pending_participants is not None:
        rule.pending_participants = new_pending_participants
    if new_participant_values is not None:
        rule.participant_values = {k: str(v) for k, v in new_participant_values.items()}
    if new_frequency is not None:
        rule.frequency = new_frequency
    if clear_end_date:
        rule.end_date = None
    elif new_end_date is not None:
        rule.end_date = new_end_date

    if new_created_by is not None or new_created_by_pending is not None:
        if new_created_by_pending:
            rule.created_by = None
            rule.created_by_email_ref = email_reference(new_created_by_pending["email"])
            rule.created_by_name_snapshot = new_created_by_pending["name"]
        else:
            payer = await db.get(User, new_created_by)
            rule.created_by = new_created_by
            rule.created_by_email_ref = email_reference(payer.email) if payer else ""
            rule.created_by_name_snapshot = payer.display_name if payer else "Unknown"

    await db.commit()
    await db.refresh(rule)
    return rule


async def list_recurring_rules(db: AsyncSession, *, group_id: str) -> list[SharedRecurringRule]:
    result = await db.execute(
        select(SharedRecurringRule).where(SharedRecurringRule.group_id == group_id).order_by(SharedRecurringRule.created_at)
    )
    return list(result.scalars().all())


async def set_recurring_rule_active(db: AsyncSession, *, rule_id: str, active: bool) -> SharedRecurringRule | None:
    """
    Pausing/resuming, not deleting — a paused rule (e.g. a subscription
    on hold, a roommate moving out temporarily) stops generating new
    expenses but its own row and every expense it already materialized
    stay exactly as they are. This is the RIGHT tool for a temporary
    hold specifically; delete_recurring_rule (below) exists separately
    for the different, real case of "this was a mistake, remove it
    entirely" — not offering both was the original design, revisited
    after being asked for it directly: "why did rent stop appearing"
    is a more confusing question to answer from silence than from a
    rule that's visibly still there, paused, which is exactly why
    pause stays the default suggestion — but a genuinely wrong rule
    (typo'd amount, wrong people, created in the wrong group) has no
    reason to sit around forever just because deletion wasn't offered.
    """
    rule = await db.get(SharedRecurringRule, rule_id)
    if rule is None:
        return None
    rule.active = active
    await db.commit()
    await db.refresh(rule)
    return rule


async def delete_recurring_rule(db: AsyncSession, *, rule_id: str) -> bool:
    """
    Deletes the SCHEDULE only — every expense it already materialized
    is a real, independent SharedExpense row with its own splits and
    its own delete path, and none of that is touched here. Deleting
    the rule just means no FUTURE occurrences get generated; it is not
    a way to retroactively undo history, the same principle
    set_recurring_rule_active's own docstring already establishes for
    pausing. Returns whether a rule was actually found and deleted, so
    the router can 404 correctly rather than silently no-op.
    """
    rule = await db.get(SharedRecurringRule, rule_id)
    if rule is None:
        return False
    await db.delete(rule)
    await db.commit()
    return True


async def materialize_due_shared_expenses(db: AsyncSession, *, group_id: str) -> list[SharedExpense]:
    """
    Lazy catch-up, exactly like the personal ledger's client-side
    engine: called whenever a group's expenses or balances are read
    (not on a schedule/cron — no background job infrastructure exists
    for this project, and lazy catch-up needs none, because whoever
    next opens the group triggers it and every due occurrence since
    last_materialized gets generated with its correct historical date
    regardless of how long it's been). Returns whatever new expenses
    were just created, in case a caller wants to surface "3 new bills
    were added" -- most callers can ignore the return value.

    Each occurrence becomes a real SharedExpense via
    create_shared_expense() -- the exact same function a person
    manually adding an expense calls -- not a parallel code path. A
    materialized rent payment IS a shared expense in every way once
    created; nothing downstream needs to know or care that it came
    from a rule.
    """
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    result = await db.execute(
        select(SharedRecurringRule).where(SharedRecurringRule.group_id == group_id, SharedRecurringRule.active.is_(True))
    )
    rules = list(result.scalars().all())

    created: list[SharedExpense] = []
    for rule in rules:
        if rule.created_by is None:
            # Creator's account was deleted (frozen, not cascaded --
            # see freeze_user_references) -- no live payer to attribute
            # new occurrences to. The rule and its past expenses stay
            # exactly as they are; it simply stops generating new ones,
            # same spirit as an explicitly paused rule.
            continue

        occurrences = due_occurrences(
            start_date=rule.start_date, frequency=rule.frequency, end_date=rule.end_date,
            last_materialized=rule.last_materialized, today=today,
        )
        if not occurrences:
            continue

        participant_values = {k: Decimal(v) for k, v in rule.participant_values.items()} if rule.participant_values else None
        for occ_date in occurrences:
            expense = await create_shared_expense(
                db,
                group_id=group_id,
                paid_by=rule.created_by,
                description=rule.description,
                amount=Decimal(str(rule.amount)),
                expense_date=occ_date,
                participant_ids=rule.participant_ids,
                pending_participants=rule.pending_participants,
                category=rule.category,
                split_type=rule.split_type,
                participant_values=participant_values,
            )
            created.append(expense)
            rule.last_materialized = occ_date

    if created:
        await db.commit()
    return created
