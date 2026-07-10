"""
Shared-expense business logic. Two pieces worth understanding before
touching this file:

1. SPLITTING MATH uses Decimal throughout, never float — an even
   split of $100 three ways is $33.33/$33.33/$33.34, and float
   arithmetic on money is exactly the class of bug that produces
   $33.330000000000005 instead. The largest-remainder method
   (split_evenly) guarantees the parts always sum EXACTLY to the
   total, with the leftover cents distributed deterministically
   (largest fractional remainder first) rather than "doesn't matter
   who" actually meaning "arbitrary" — it's still a real, repeatable
   algorithm, just not one that favors any particular participant.

2. THE FREEZE-NOT-CASCADE DELETION POLICY. freeze_user_references()
   is the one integration point this module expects auth_service to
   call before deleting a user row — see the docstring on
   SharedExpenseSplit for the full reasoning. This function is the
   only thing outside this module that needs to know shared-expenses
   exists at all, which is deliberate: keeps the coupling to exactly
   one narrow, well-defined call.
"""

from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.group import Group
from app.models.group_member import GroupMember
from app.models.settlement import Settlement
from app.models.shared_expense import SharedExpense
from app.models.shared_expense_comment import SharedExpenseComment
from app.models.shared_expense_split import SharedExpenseSplit
from app.models.user import User


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

    # Fractional remainder each participant "lost" to the floor
    # division — largest remainder gets the extra penny first.
    exact_share = total / n
    remainders = [(pid, exact_share - Decimal(base_cents) / 100) for pid in participant_ids]
    remainders.sort(key=lambda r: r[1], reverse=True)

    shares_cents = {pid: base_cents for pid in participant_ids}
    for i in range(remainder_cents):
        shares_cents[remainders[i][0]] += 1

    return {pid: (Decimal(cents) / 100).quantize(Decimal("0.01")) for pid, cents in shares_cents.items()}


async def create_group(db: AsyncSession, *, name: str, created_by: str, member_ids: list[str]) -> Group:
    group = Group(name=name, created_by=created_by)
    db.add(group)
    await db.flush()  # need group.id before creating members

    all_members = set(member_ids) | {created_by}
    for uid in all_members:
        user = await db.get(User, uid)
        db.add(GroupMember(group_id=group.id, user_id=uid, name_snapshot=user.display_name if user else "Unknown"))

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
) -> SharedExpense:
    payer = await db.get(User, paid_by)
    expense = SharedExpense(
        group_id=group_id,
        paid_by=paid_by,
        paid_by_name_snapshot=payer.display_name if payer else "Unknown",
        description=description,
        amount=amount,
        expense_date=expense_date,
    )
    db.add(expense)
    await db.flush()

    shares = split_evenly(amount, participant_ids)
    for uid, share in shares.items():
        participant = await db.get(User, uid)
        db.add(SharedExpenseSplit(
            shared_expense_id=expense.id,
            user_id=uid,
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

        # Re-split proportionally to whoever was already in it.
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

    if changes:
        db.add(SharedExpenseComment(
            shared_expense_id=expense_id,
            user_id=edited_by,
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
        from_name_snapshot=from_user.display_name if from_user else "Unknown",
        to_user_id=to_user_id,
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

    # Expenses B paid where A had a share -> A owes B that share.
    result = await db.execute(
        select(SharedExpenseSplit, SharedExpense)
        .join(SharedExpense, SharedExpenseSplit.shared_expense_id == SharedExpense.id)
        .where(SharedExpenseSplit.user_id == user_a, SharedExpense.paid_by == user_b)
    )
    for split, _expense in result.all():
        balance += split.share_amount

    # Expenses A paid where B had a share -> B owes A that share (negative from A's view).
    result = await db.execute(
        select(SharedExpenseSplit, SharedExpense)
        .join(SharedExpense, SharedExpenseSplit.shared_expense_id == SharedExpense.id)
        .where(SharedExpenseSplit.user_id == user_b, SharedExpense.paid_by == user_a)
    )
    for split, _expense in result.all():
        balance -= split.share_amount

    # Settlements reduce whatever's owed in the direction they were paid.
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
    deleted. Snapshots the person's current display name onto every
    shared-expense record they're part of, then nulls out the user_id
    reference — the historical record survives as "Name (account
    deleted)"; their actual account does not. See
    SharedExpenseSplit's docstring for the full policy reasoning.
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
