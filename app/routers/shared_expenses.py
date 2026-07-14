"""
Shared-expenses HTTP endpoints. Every group/expense-touching route
checks group membership FIRST (is_group_member) before returning or
mutating anything — a group's financial detail must never be
reachable just by guessing its id.

paid_by can be set to any member of the group, not just the caller —
a deliberate, discussed product decision, not an oversight. The
original design had paid_by locked to the authenticated caller
specifically because auto-accept (no approval gate) was only safe
when "I paid this" was self-attested. Allowing any member to be named
as payer reopens that exact gap: someone can claim another real
member paid for something they didn't, and that member finds out only
by later noticing their balance changed, with no confirmation step in
between. Accepted knowingly as a tradeoff for a simpler add-expense
flow (letting anyone log a bill on the actual payer's behalf, a
common real case — e.g. entering a receipt for someone who forgot to)
rather than building a pending/confirm-or-dispute workflow. If this
becomes a real problem in practice, the fix is that confirmation step,
not reverting the flexibility.
"""

from decimal import Decimal, InvalidOperation

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import get_db
from app.core.deps import get_current_user
from app.core.limiter import limiter
from app.models.shared_recurring_rule import SharedRecurringRule
from app.models.user import User
from app.repositories import user_repository
from app.schemas.shared_expenses import (
    AddMemberRequest,
    BalanceOut,
    CommentCreateRequest,
    CommentOut,
    GroupCreateRequest,
    GroupMemberOut,
    GroupOut,
    GroupRenameRequest,
    InvitePreviewOut,
    MemberInvite,
    RecurringRuleCreateRequest,
    RecurringRuleEditRequest,
    RecurringRuleOut,
    SetRecurringRuleActiveRequest,
    SettlementCreateRequest,
    SettlementOut,
    SimplifiedTransferOut,
    BalanceBreakdownItemOut,
    SharedExpenseCreateRequest,
    SharedExpenseEditRequest,
    SharedExpenseOut,
    SplitOut,
)
from app.services import shared_expense_service as svc
from app.services.shared_expense_service import SplitValidationError

router = APIRouter(prefix="/shared-expenses", tags=["shared-expenses"])


def _to_decimal(value: float, field_name: str) -> Decimal:
    try:
        return Decimal(str(value)).quantize(Decimal("0.01"))
    except InvalidOperation:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=f"Invalid {field_name}")


async def _require_group_member(db: AsyncSession, *, group_id: str, user_id: str) -> None:
    if not await svc.is_group_member(db, group_id=group_id, user_id=user_id):
        # 404, not 403 — a group you're not in shouldn't even confirm
        # it exists, same enumeration-safety principle used elsewhere
        # in this app (login, signup, forgot-password).
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Group not found")


async def _group_to_out(db: AsyncSession, group) -> GroupOut:
    members = await svc.get_group_members(db, group_id=group.id)
    pending = await svc.get_group_pending_invites(db, group_id=group.id)
    # One batch query for every real member's avatar rather than N
    # individual lookups — avatars are looked up LIVE (never
    # snapshotted, unlike name_snapshot) so a member's current photo
    # always shows, not whatever it was when they joined.
    real_ids = [m.user_id for m in members if m.user_id]
    avatars: dict[str, str | None] = {}
    if real_ids:
        result = await db.execute(select(User.id, User.avatar_data).where(User.id.in_(real_ids)))
        avatars = dict(result.all())
    return GroupOut(
        id=group.id,
        name=group.name,
        members=[GroupMemberOut(user_id=m.user_id, name=m.name_snapshot, avatar_data=avatars.get(m.user_id)) for m in members],
        pending_invites=pending,
        created_at=group.created_at,
    )


@router.post("/groups", response_model=GroupOut, status_code=status.HTTP_201_CREATED)
@limiter.limit("30/minute")
async def create_group(request: Request, 
    payload: GroupCreateRequest,
    background_tasks: BackgroundTasks,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> GroupOut:
    member_ids = []
    pending = []
    for m in payload.members:
        user = await user_repository.get_by_email(db, m.email.lower())
        if user is None:
            pending.append(m)
        else:
            member_ids.append(user.id)

    group = await svc.create_group(db, name=payload.name, created_by=current_user.id, member_ids=member_ids)

    # Invites are queued AFTER the group exists — each one needs a
    # real group_id to attach to, and the group's own name for the
    # invite email's subject line.
    for m in pending:
        await svc.create_pending_invite(
            db, background_tasks, group_id=group.id, email=m.email, name=m.name, invited_by=current_user.id, group_name=group.name, frontend_url=settings.frontend_url
        )

    return await _group_to_out(db, group)


@router.get("/groups", response_model=list[GroupOut])
@limiter.limit("120/minute")
async def list_my_groups(request: Request, current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)) -> list[GroupOut]:
    groups = await svc.get_user_groups(db, user_id=current_user.id)
    return [await _group_to_out(db, group) for group in groups]


@router.get("/groups/{group_id}", response_model=GroupOut)
@limiter.limit("120/minute")
async def get_group_detail(request: Request, 
    group_id: str, current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
) -> GroupOut:
    await _require_group_member(db, group_id=group_id, user_id=current_user.id)
    group = await svc.get_group(db, group_id=group_id)
    if group is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Group not found")
    return await _group_to_out(db, group)


@router.post("/groups/{group_id}/members", response_model=GroupOut, status_code=status.HTTP_201_CREATED)
@limiter.limit("30/minute")
async def add_group_member(request: Request, 
    group_id: str,
    payload: AddMemberRequest,
    background_tasks: BackgroundTasks,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> GroupOut:
    """
    The gap this closes: member_emails on GroupCreateRequest only
    ever ran at creation time — there was no way to add someone to a
    group after the fact, reported directly. Same resolve-or-invite
    logic as create_group (existing account -> real member,
    no account yet -> pending invite + email), just against a group
    that already exists.
    """
    await _require_group_member(db, group_id=group_id, user_id=current_user.id)
    group = await svc.get_group(db, group_id=group_id)
    if group is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Group not found")

    user = await user_repository.get_by_email(db, payload.email.lower())
    if user is not None:
        await svc.add_member_to_group(db, group_id=group_id, user_id=user.id)
    else:
        await svc.ensure_pending_invite(
            db, background_tasks, group_id=group_id, email=payload.email, name=payload.name,
            invited_by=current_user.id, group_name=group.name, frontend_url=settings.frontend_url,
        )

    return await _group_to_out(db, group)


@router.delete("/groups/{group_id}/members/{user_id}", response_model=GroupOut)
@limiter.limit("30/minute")
async def remove_group_member(request: Request, 
    group_id: str, user_id: str, current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
) -> GroupOut:
    """
    Same rule group deletion already enforces, one person at a time:
    removing someone who has real expense history in this group would
    silently orphan a debt (their split would still exist and still
    count toward balances, but they'd no longer even be listed as
    part of the group). Anyone in the group can remove anyone else —
    including themselves — as long as this check clears; there's no
    special protection for the group's creator.
    """
    await _require_group_member(db, group_id=group_id, user_id=current_user.id)
    if await svc.member_has_expense_history(db, group_id=group_id, user_id=user_id):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail="Can't remove this person — they have shared expense history in this group.",
        )
    await svc.remove_member_from_group(db, group_id=group_id, user_id=user_id)
    group = await svc.get_group(db, group_id=group_id)
    if group is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Group not found")
    return await _group_to_out(db, group)


@router.delete("/groups/{group_id}/pending-invites", response_model=GroupOut)
@limiter.limit("30/minute")
async def remove_pending_invite(request: Request, 
    group_id: str, email: str, current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
) -> GroupOut:
    """Same expense-history protection as remove_group_member, for someone who was never a real member — just an invite."""
    await _require_group_member(db, group_id=group_id, user_id=current_user.id)
    if await svc.pending_invite_has_expense_history(db, group_id=group_id, email=email):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail="Can't remove this invite — they already have shared expense history in this group.",
        )
    await svc.remove_pending_invite(db, group_id=group_id, email=email)
    group = await svc.get_group(db, group_id=group_id)
    if group is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Group not found")
    return await _group_to_out(db, group)


@router.get("/invites/{invite_id}", response_model=InvitePreviewOut)
@limiter.limit("30/minute")
async def get_invite_preview(request: Request, invite_id: str, db: AsyncSession = Depends(get_db)) -> InvitePreviewOut:
    """
    Deliberately unauthenticated and deliberately NOT gated by
    _require_group_member — the whole point is that someone with no
    account yet, who isn't a member of anything, needs to see "you've
    been invited to X by Y" before they've signed in. See
    svc.get_invite_preview's docstring for why this is safe.
    """
    if not settings.invite_links_enabled:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="This invite link is invalid or has already been used.")
    preview = await svc.get_invite_preview(db, invite_id=invite_id)
    if preview is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="This invite link is invalid or has already been used.")
    return InvitePreviewOut(**preview)


@router.post("/invites/{invite_id}/accept", response_model=GroupOut)
@limiter.limit("30/minute")
async def accept_invite(
    request: Request, invite_id: str, current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
) -> GroupOut:
    """Authenticated — requires the caller to have actually signed in or signed up first, not just held the link."""
    if not settings.invite_links_enabled:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="This invite link is invalid or has already been used.")
    group = await svc.accept_invite_link(db, invite_id=invite_id, user=current_user)
    if group is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="This invite link is invalid or has already been used.")
    return await _group_to_out(db, group)


@router.patch("/groups/{group_id}", response_model=GroupOut)
@limiter.limit("30/minute")
async def rename_group(request: Request, 
    group_id: str, payload: GroupRenameRequest, current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
) -> GroupOut:
    await _require_group_member(db, group_id=group_id, user_id=current_user.id)
    group = await svc.rename_group(db, group_id=group_id, new_name=payload.name)
    return await _group_to_out(db, group)


@router.delete("/groups/{group_id}", status_code=status.HTTP_204_NO_CONTENT)
@limiter.limit("30/minute")
async def delete_group(request: Request, 
    group_id: str, current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
) -> None:
    """
    Only EMPTY groups are deletable — a group with expense history is
    a shared record belonging to every member, and one person
    removing it would erase everyone else's view of real debts. An
    empty group is just a container (this also covers cleaning up
    duplicates created by the earlier email-send bug, where retries
    after '500-but-the-group-was-actually-created' left several empty
    copies behind). Any member can delete an empty group, not just
    the creator — an empty group holds nothing whose removal could
    disadvantage anyone.
    """
    await _require_group_member(db, group_id=group_id, user_id=current_user.id)
    expenses = await svc.get_group_expenses(db, group_id=group_id)
    if expenses:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail="This group has expense history and can't be deleted — that record belongs to everyone in it.",
        )
    await svc.delete_group(db, group_id=group_id)


async def _expense_to_out(db: AsyncSession, expense, splits) -> SharedExpenseOut:
    real_ids = [s.user_id for s in splits if s.user_id]
    avatars: dict[str, str | None] = {}
    if real_ids:
        result = await db.execute(select(User.id, User.avatar_data).where(User.id.in_(real_ids)))
        avatars = dict(result.all())
    return SharedExpenseOut(
        id=expense.id,
        group_id=expense.group_id,
        paid_by=expense.paid_by,
        paid_by_name=expense.paid_by_name_snapshot,
        description=expense.description,
        category=expense.category,
        split_type=expense.split_type,
        amount=str(expense.amount),
        expense_date=expense.expense_date,
        splits=[SplitOut(user_id=s.user_id, name=s.name_snapshot, share_amount=str(s.share_amount), avatar_data=avatars.get(s.user_id)) for s in splits],
        created_at=expense.created_at,
        updated_at=expense.updated_at,
    )


@router.post("/groups/{group_id}/expenses", response_model=SharedExpenseOut, status_code=status.HTTP_201_CREATED)
@limiter.limit("60/minute")
async def create_expense(request: Request, 
    group_id: str,
    payload: SharedExpenseCreateRequest,
    background_tasks: BackgroundTasks,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> SharedExpenseOut:
    await _require_group_member(db, group_id=group_id, user_id=current_user.id)

    group_member_ids = {m.user_id for m in await svc.get_group_members(db, group_id=group_id) if m.user_id}
    invalid = set(payload.participant_ids) - group_member_ids
    if invalid:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="All participants must be members of this group")

    # Defaults to the caller when omitted (the ORIGINAL, still-common
    # case: you're logging your own expense). See this module's own
    # docstring for the deliberate tradeoff in allowing any OTHER real
    # member to be named here too. paid_by_pending is a separate path
    # entirely (validated as mutually exclusive with paid_by by the
    # schema) — a real user_id makes no sense to require in that case.
    paid_by = None if payload.paid_by_pending else (payload.paid_by or current_user.id)
    if paid_by is not None and paid_by not in group_member_ids:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="paid_by must be a member of this group")

    group = await svc.get_group(db, group_id=group_id)

    # A pending participant doesn't need to already be a member (or
    # even already invited) to this group — splitting an expense with
    # someone new invites them on the spot, same as adding them
    # directly. ensure_pending_invite is a no-op if they're already
    # pending for this group (no duplicate row, no second email).
    for p in payload.pending_participants:
        await svc.ensure_pending_invite(
            db, background_tasks, group_id=group_id, email=p.email, name=p.name,
            invited_by=current_user.id, group_name=group.name if group else "", frontend_url=settings.frontend_url,
        )
    # Naming someone not yet in the group as the PAYER invites them the
    # exact same way — same reasoning as pending participants above,
    # just applied to who paid instead of who's splitting it.
    if payload.paid_by_pending:
        await svc.ensure_pending_invite(
            db, background_tasks, group_id=group_id, email=payload.paid_by_pending.email, name=payload.paid_by_pending.name,
            invited_by=current_user.id, group_name=group.name if group else "", frontend_url=settings.frontend_url,
        )

    try:
        expense = await svc.create_shared_expense(
            db,
            group_id=group_id,
            paid_by=paid_by,  # defaults to the caller, but may be any validated group member, or None when paid_by_pending is set — see module docstring
            description=payload.description,
            amount=_to_decimal(payload.amount, "amount"),
            expense_date=payload.expense_date,
            participant_ids=payload.participant_ids,
            pending_participants=[{"email": p.email, "name": p.name} for p in payload.pending_participants],
            category=payload.category,
            split_type=payload.split_type,
            participant_values={k: _to_decimal(v, "participant_values") for k, v in payload.participant_values.items()},
            paid_by_pending={"email": payload.paid_by_pending.email, "name": payload.paid_by_pending.name} if payload.paid_by_pending else None,
        )
    except SplitValidationError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(e))
    splits = await svc.get_expense_splits(db, expense_id=expense.id)
    return await _expense_to_out(db, expense, splits)


@router.get("/groups/{group_id}/expenses", response_model=list[SharedExpenseOut])
@limiter.limit("120/minute")
async def list_group_expenses(request: Request, 
    group_id: str, current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
) -> list[SharedExpenseOut]:
    await _require_group_member(db, group_id=group_id, user_id=current_user.id)
    await svc.materialize_due_shared_expenses(db, group_id=group_id)
    expenses = await svc.get_group_expenses(db, group_id=group_id)
    result = []
    for e in expenses:
        splits = await svc.get_expense_splits(db, expense_id=e.id)
        result.append(await _expense_to_out(db, e, splits))
    return result


def _rule_to_out(rule) -> RecurringRuleOut:
    return RecurringRuleOut(
        id=rule.id,
        group_id=rule.group_id,
        created_by=rule.created_by,
        created_by_name=rule.created_by_name_snapshot,
        description=rule.description,
        amount=str(rule.amount),
        category=rule.category,
        split_type=rule.split_type,
        participant_ids=rule.participant_ids,
        pending_participants=[MemberInvite(**p) for p in rule.pending_participants],
        participant_values=rule.participant_values,
        frequency=rule.frequency,
        start_date=rule.start_date,
        end_date=rule.end_date,
        last_materialized=rule.last_materialized,
        active=rule.active,
        created_at=rule.created_at,
    )


@router.post("/groups/{group_id}/recurring", response_model=RecurringRuleOut, status_code=status.HTTP_201_CREATED)
@limiter.limit("30/minute")
async def create_recurring_rule(request: Request, 
    group_id: str, payload: RecurringRuleCreateRequest, background_tasks: BackgroundTasks, current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
) -> RecurringRuleOut:
    await _require_group_member(db, group_id=group_id, user_id=current_user.id)

    group_member_ids = {m.user_id for m in await svc.get_group_members(db, group_id=group_id) if m.user_id}
    if payload.paid_by is not None and payload.paid_by not in group_member_ids:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="paid_by must be a member of this group")
    if payload.paid_by_pending:
        group = await svc.get_group(db, group_id=group_id)
        await svc.ensure_pending_invite(
            db, background_tasks, group_id=group_id, email=payload.paid_by_pending.email, name=payload.paid_by_pending.name,
            invited_by=current_user.id, group_name=group.name if group else "", frontend_url=settings.frontend_url,
        )

    try:
        rule = await svc.create_recurring_rule(
            db,
            group_id=group_id,
            created_by=None if payload.paid_by_pending else (payload.paid_by or current_user.id),
            description=payload.description,
            amount=_to_decimal(payload.amount, "amount"),
            category=payload.category,
            split_type=payload.split_type,
            participant_ids=payload.participant_ids,
            pending_participants=[{"email": p.email, "name": p.name} for p in payload.pending_participants],
            participant_values={k: _to_decimal(v, "participant_values") for k, v in payload.participant_values.items()},
            frequency=payload.frequency,
            start_date=payload.start_date,
            end_date=payload.end_date,
            created_by_pending={"email": payload.paid_by_pending.email, "name": payload.paid_by_pending.name} if payload.paid_by_pending else None,
        )
    except SplitValidationError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(e))
    # A newly created rule may already have occurrences due (e.g. start_date
    # in the past) — materialize immediately rather than waiting for the
    # next unrelated read of this group's expenses/balances.
    await svc.materialize_due_shared_expenses(db, group_id=group_id)
    return _rule_to_out(rule)


@router.get("/groups/{group_id}/recurring", response_model=list[RecurringRuleOut])
@limiter.limit("120/minute")
async def list_recurring_rules(request: Request, 
    group_id: str, current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
) -> list[RecurringRuleOut]:
    await _require_group_member(db, group_id=group_id, user_id=current_user.id)
    rules = await svc.list_recurring_rules(db, group_id=group_id)
    return [_rule_to_out(r) for r in rules]


@router.patch("/recurring/{rule_id}", response_model=RecurringRuleOut)
@limiter.limit("30/minute")
async def edit_recurring_rule(request: Request, 
    rule_id: str, payload: RecurringRuleEditRequest, background_tasks: BackgroundTasks, current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
) -> RecurringRuleOut:
    rule = await db.get(SharedRecurringRule, rule_id)
    if rule is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Recurring rule not found")
    await _require_group_member(db, group_id=rule.group_id, user_id=current_user.id)

    participants_changing = payload.participant_ids is not None or payload.pending_participants is not None
    paid_by_changing = payload.paid_by is not None or payload.paid_by_pending is not None
    group = None

    if participants_changing or paid_by_changing:
        group_member_ids = {m.user_id for m in await svc.get_group_members(db, group_id=rule.group_id) if m.user_id}
        group = await svc.get_group(db, group_id=rule.group_id)

    if participants_changing:
        real_ids = payload.participant_ids if payload.participant_ids is not None else []
        pending = payload.pending_participants if payload.pending_participants is not None else []
        if not real_ids and not pending:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="At least one participant is required")
        invalid = set(real_ids) - group_member_ids
        if invalid:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="All participants must be members of this group")
        for p in pending:
            await svc.ensure_pending_invite(
                db, background_tasks, group_id=rule.group_id, email=p.email, name=p.name,
                invited_by=current_user.id, group_name=group.name if group else "", frontend_url=settings.frontend_url,
            )

    if payload.paid_by is not None and payload.paid_by not in group_member_ids:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="paid_by must be a member of this group")
    if payload.paid_by_pending:
        await svc.ensure_pending_invite(
            db, background_tasks, group_id=rule.group_id, email=payload.paid_by_pending.email, name=payload.paid_by_pending.name,
            invited_by=current_user.id, group_name=group.name if group else "", frontend_url=settings.frontend_url,
        )

    try:
        updated = await svc.edit_recurring_rule(
            db, rule_id=rule_id,
            new_description=payload.description,
            new_amount=_to_decimal(payload.amount, "amount") if payload.amount is not None else None,
            new_category=payload.category,
            new_split_type=payload.split_type,
            new_participant_ids=payload.participant_ids,
            new_pending_participants=[{"email": p.email, "name": p.name} for p in payload.pending_participants] if payload.pending_participants is not None else None,
            new_participant_values={k: _to_decimal(v, "participant_values") for k, v in payload.participant_values.items()} if payload.participant_values is not None else None,
            new_frequency=payload.frequency,
            new_end_date=payload.end_date,
            clear_end_date=payload.clear_end_date,
            new_created_by=payload.paid_by,
            new_created_by_pending={"email": payload.paid_by_pending.email, "name": payload.paid_by_pending.name} if payload.paid_by_pending else None,
        )
    except SplitValidationError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(e))
    except ValueError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(e))
    return _rule_to_out(updated)


@router.patch("/recurring/{rule_id}/active", response_model=RecurringRuleOut)
@limiter.limit("30/minute")
async def set_recurring_rule_active(request: Request, 
    rule_id: str, payload: SetRecurringRuleActiveRequest, current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
) -> RecurringRuleOut:
    rule = await db.get(SharedRecurringRule, rule_id)
    if rule is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Recurring rule not found")
    await _require_group_member(db, group_id=rule.group_id, user_id=current_user.id)
    updated = await svc.set_recurring_rule_active(db, rule_id=rule_id, active=payload.active)
    return _rule_to_out(updated)


@router.delete("/recurring/{rule_id}", status_code=status.HTTP_204_NO_CONTENT)
@limiter.limit("30/minute")
async def delete_recurring_rule(request: Request, 
    rule_id: str, current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
) -> None:
    rule = await db.get(SharedRecurringRule, rule_id)
    if rule is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Recurring rule not found")
    await _require_group_member(db, group_id=rule.group_id, user_id=current_user.id)
    await svc.delete_recurring_rule(db, rule_id=rule_id)


async def _require_expense_access(db: AsyncSession, *, expense_id: str, user_id: str):
    expense = await svc.get_expense(db, expense_id=expense_id)
    if expense is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Expense not found")
    await _require_group_member(db, group_id=expense.group_id, user_id=user_id)
    return expense


@router.get("/expenses/{expense_id}", response_model=SharedExpenseOut)
@limiter.limit("120/minute")
async def get_expense_detail(request: Request, 
    expense_id: str, current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
) -> SharedExpenseOut:
    expense = await _require_expense_access(db, expense_id=expense_id, user_id=current_user.id)
    splits = await svc.get_expense_splits(db, expense_id=expense_id)
    return await _expense_to_out(db, expense, splits)


@router.patch("/expenses/{expense_id}", response_model=SharedExpenseOut)
@limiter.limit("30/minute")
async def edit_expense(request: Request, 
    expense_id: str,
    payload: SharedExpenseEditRequest,
    background_tasks: BackgroundTasks,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> SharedExpenseOut:
    expense = await _require_expense_access(db, expense_id=expense_id, user_id=current_user.id)
    new_amount = _to_decimal(payload.amount, "amount") if payload.amount is not None else None

    participants_changing = payload.participant_ids is not None or payload.pending_participants is not None
    paid_by_changing = payload.paid_by is not None or payload.paid_by_pending is not None
    group = None

    if participants_changing or paid_by_changing:
        group_member_ids = {m.user_id for m in await svc.get_group_members(db, group_id=expense.group_id) if m.user_id}
        group = await svc.get_group(db, group_id=expense.group_id)

    if participants_changing:
        real_ids = payload.participant_ids or []
        pending = payload.pending_participants or []
        if not real_ids and not pending:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="At least one participant is required")

        invalid = set(real_ids) - group_member_ids
        if invalid:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="All participants must be members of this group")

        for p in pending:
            await svc.ensure_pending_invite(
                db, background_tasks, group_id=expense.group_id, email=p.email, name=p.name,
                invited_by=current_user.id, group_name=group.name if group else "", frontend_url=settings.frontend_url,
            )

    if payload.paid_by is not None and payload.paid_by not in group_member_ids:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="paid_by must be a member of this group")
    if payload.paid_by_pending:
        await svc.ensure_pending_invite(
            db, background_tasks, group_id=expense.group_id, email=payload.paid_by_pending.email, name=payload.paid_by_pending.name,
            invited_by=current_user.id, group_name=group.name if group else "", frontend_url=settings.frontend_url,
        )

    try:
        updated = await svc.edit_shared_expense(
            db, expense_id=expense_id, edited_by=current_user.id,
            new_amount=new_amount, new_description=payload.description, new_category=payload.category,
            new_expense_date=payload.expense_date,
            new_participant_ids=payload.participant_ids,
            new_pending_participants=[{"email": p.email, "name": p.name} for p in payload.pending_participants] if payload.pending_participants is not None else None,
            new_split_type=payload.split_type,
            new_participant_values={k: _to_decimal(v, "participant_values") for k, v in payload.participant_values.items()} if payload.participant_values is not None else None,
            new_paid_by=payload.paid_by,
            new_paid_by_pending={"email": payload.paid_by_pending.email, "name": payload.paid_by_pending.name} if payload.paid_by_pending else None,
        )
    except SplitValidationError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(e))
    splits = await svc.get_expense_splits(db, expense_id=expense_id)
    return await _expense_to_out(db, updated, splits)


@router.delete("/expenses/{expense_id}", status_code=status.HTTP_204_NO_CONTENT)
@limiter.limit("30/minute")
async def delete_expense(request: Request, 
    expense_id: str, current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
) -> None:
    """
    Unlike a group or a member, an individual expense has no
    expense-history-of-its-own to protect — deleting it IS the
    action, not something that could orphan a deeper record. Any
    group member can delete it, same as any other expense action.
    """
    await _require_expense_access(db, expense_id=expense_id, user_id=current_user.id)
    await svc.delete_shared_expense(db, expense_id=expense_id)


@router.get("/expenses/{expense_id}/comments", response_model=list[CommentOut])
@limiter.limit("120/minute")
async def list_comments(request: Request, 
    expense_id: str, current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
) -> list[CommentOut]:
    await _require_expense_access(db, expense_id=expense_id, user_id=current_user.id)
    comments = await svc.get_expense_comments(db, expense_id=expense_id)
    return [CommentOut(id=c.id, user_id=c.user_id, name=c.name_snapshot, body=c.body, is_system=c.is_system, created_at=c.created_at) for c in comments]


@router.post("/expenses/{expense_id}/comments", response_model=CommentOut, status_code=status.HTTP_201_CREATED)
@limiter.limit("60/minute")
async def add_comment(request: Request, 
    expense_id: str,
    payload: CommentCreateRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> CommentOut:
    await _require_expense_access(db, expense_id=expense_id, user_id=current_user.id)
    comment = await svc.add_comment(db, expense_id=expense_id, user_id=current_user.id, body=payload.body)
    return CommentOut(id=comment.id, user_id=comment.user_id, name=comment.name_snapshot, body=comment.body, is_system=comment.is_system, created_at=comment.created_at)


@router.post("/settlements", response_model=SettlementOut, status_code=status.HTTP_201_CREATED)
@limiter.limit("30/minute")
async def record_settlement(request: Request, 
    payload: SettlementCreateRequest, current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
) -> SettlementOut:
    if bool(payload.counterparty_user_id) == bool(payload.counterparty_email_ref):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="Provide exactly one of counterparty_user_id or counterparty_email_ref")
    if payload.direction not in ("i_paid_them", "they_paid_me"):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="direction must be 'i_paid_them' or 'they_paid_me'")

    counterparty_name: str | None = None
    if payload.counterparty_user_id:
        counterparty = await user_repository.get_by_id(db, payload.counterparty_user_id)
        if counterparty is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="User not found")
        if payload.direction == "they_paid_me":
            # A real, signed-up counterparty can log in and confirm this
            # themselves via "i_paid_them" on their own account —
            # recording it unilaterally here would be one person
            # silently editing the other side of a mutual ledger.
            raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="Ask them to record this from their own account — recording a real user's payment on their behalf isn't supported")
    else:
        # Pending/frozen counterparty — never trust a client-supplied
        # name for someone else's identity. The name is only ever
        # derived from real shared-expense history the caller actually
        # has with this specific email_ref; if there's no match, this
        # isn't a real counterparty this user has any connection to.
        counterparty_name = await svc.find_pending_or_frozen_name(db, user_id=current_user.id, email_ref=payload.counterparty_email_ref)
        if counterparty_name is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="No shared expense history found with that person")

    if payload.direction == "i_paid_them":
        settle_kwargs = dict(
            from_user_id=current_user.id,
            to_user_id=payload.counterparty_user_id,
            to_email_ref=payload.counterparty_email_ref,
            to_name=counterparty_name,
        )
    else:
        settle_kwargs = dict(
            from_email_ref=payload.counterparty_email_ref,
            from_name=counterparty_name,
            to_user_id=current_user.id,
        )

    settlement = await svc.record_settlement(
        db,
        amount=_to_decimal(payload.amount, "amount"),
        settled_date=payload.settled_date,
        **settle_kwargs,
    )
    return SettlementOut(
        id=settlement.id,
        from_user_id=settlement.from_user_id,
        from_name=settlement.from_name_snapshot,
        to_user_id=settlement.to_user_id,
        to_name=settlement.to_name_snapshot,
        amount=str(settlement.amount),
        settled_date=settlement.settled_date,
    )


@router.get("/settlements/received", response_model=list[SettlementOut])
@limiter.limit("60/minute")
async def list_settlements_received(
    request: Request, current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
) -> list[SettlementOut]:
    """
    Every settlement paid TO this user, regardless of who recorded it
    (always the payer, on their own device -- see
    svc.get_settlements_received's docstring for why this endpoint
    needs to exist at all: Sanchay is local-first, so a settlement
    someone else recorded has no way to reach this user's own local
    ledger except by this user's own app noticing it's missing and
    prompting them to record where the money landed).
    """
    settlements = await svc.get_settlements_received(db, user_id=current_user.id)
    return [
        SettlementOut(
            id=s.id, from_user_id=s.from_user_id, from_name=s.from_name_snapshot,
            to_user_id=s.to_user_id, to_name=s.to_name_snapshot, amount=str(s.amount), settled_date=s.settled_date,
        )
        for s in settlements
    ]


@router.get("/balances", response_model=list[BalanceOut])
@limiter.limit("120/minute")
async def my_balances(request: Request, current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)) -> list[BalanceOut]:
    for group in await svc.get_user_groups(db, user_id=current_user.id):
        await svc.materialize_due_shared_expenses(db, group_id=group.id)
    balances = await svc.get_all_balances(db, user_id=current_user.id)
    result = []
    for b in balances:
        balance = b["balance"]  # positive = current_user owes them; negative = they owe current_user
        result.append(BalanceOut(
            user_id=b["user_id"],
            email_ref=b["email_ref"],
            name=b["name"],
            you_owe_them=str(max(balance, Decimal("0.00"))),
            they_owe_you=str(max(-balance, Decimal("0.00"))),
            is_frozen=b["is_frozen"],
        ))
    return result


@router.get("/groups/{group_id}/simplified-debts", response_model=list[SimplifiedTransferOut])
@limiter.limit("60/minute")
async def get_simplified_debts(
    request: Request, group_id: str, current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
) -> list[SimplifiedTransferOut]:
    """
    "Here's the minimum set of payments to settle this group up,"
    not one pairwise balance per person. See
    svc.compute_group_debt_simplification's docstring for the honest
    scoping limitation: based on this group's own expenses only, not
    settlements (which are cross-group by design and can't be
    attributed to one group specifically).
    """
    await _require_group_member(db, group_id=group_id, user_id=current_user.id)
    transfers = await svc.compute_group_debt_simplification(db, group_id=group_id)
    return [
        SimplifiedTransferOut(
            from_user_id=None if t["from_key"].startswith("frozen:") else t["from_key"],
            from_name=t["from_name"],
            to_user_id=None if t["to_key"].startswith("frozen:") else t["to_key"],
            to_name=t["to_name"],
            amount=str(t["amount"]),
        )
        for t in transfers
    ]


@router.get("/balances/{other_user_id}/breakdown", response_model=list[BalanceBreakdownItemOut])
@limiter.limit("60/minute")
async def get_balance_breakdown_route(
    request: Request, other_user_id: str, current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
) -> list[BalanceBreakdownItemOut]:
    """
    "Here are the actual expenses and settlements behind that number,"
    not just the net total -- trust is the real product in shared
    money, and a receipt is more convincing than an unexplained sum.
    Deliberately does NOT require the two people to share a group in
    common at request time (they might no longer, e.g. after leaving
    it) -- if compute_balance ever considered an expense between them,
    this shows it too, using the exact same underlying query logic.
    """
    items = await svc.get_balance_breakdown(db, user_id=current_user.id, other_user_id=other_user_id)
    return [
        BalanceBreakdownItemOut(
            type=i["type"], date=i["date"], group_name=i["group_name"], description=i["description"],
            amount=str(i["amount"]), direction=i["direction"],
        )
        for i in items
    ]
