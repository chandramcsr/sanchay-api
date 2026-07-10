"""
Shared-expenses HTTP endpoints. Every group/expense-touching route
checks group membership FIRST (is_group_member) before returning or
mutating anything — a group's financial detail must never be
reachable just by guessing its id.

paid_by is always the authenticated caller, never a parameter someone
else could set on your behalf — auto-accept (no approval gate) is
only safe because "I paid this" is self-attested, never a claim about
someone else that they'd need to confirm.
"""

from decimal import Decimal, InvalidOperation

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import get_db
from app.core.deps import get_current_user
from app.models.user import User
from app.repositories import user_repository
from app.schemas.shared_expenses import (
    BalanceOut,
    CommentCreateRequest,
    CommentOut,
    GroupCreateRequest,
    GroupMemberOut,
    GroupOut,
    GroupRenameRequest,
    SettlementCreateRequest,
    SettlementOut,
    SharedExpenseCreateRequest,
    SharedExpenseEditRequest,
    SharedExpenseOut,
    SplitOut,
)
from app.services import shared_expense_service as svc

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
    return GroupOut(
        id=group.id,
        name=group.name,
        members=[GroupMemberOut(user_id=m.user_id, name=m.name_snapshot) for m in members],
        pending_invites=pending,
        created_at=group.created_at,
    )


@router.post("/groups", response_model=GroupOut, status_code=status.HTTP_201_CREATED)
async def create_group(
    payload: GroupCreateRequest,
    background_tasks: BackgroundTasks,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> GroupOut:
    member_ids = []
    pending_emails = []
    for email in payload.member_emails:
        user = await user_repository.get_by_email(db, email.lower())
        if user is None:
            pending_emails.append(email)
        else:
            member_ids.append(user.id)

    group = await svc.create_group(db, name=payload.name, created_by=current_user.id, member_ids=member_ids)

    # Invites are queued AFTER the group exists — each one needs a
    # real group_id to attach to, and the group's own name for the
    # invite email's subject line.
    for email in pending_emails:
        await svc.create_pending_invite(
            db, background_tasks, group_id=group.id, email=email, invited_by=current_user.id, group_name=group.name, frontend_url=settings.frontend_url
        )

    return await _group_to_out(db, group)


@router.get("/groups", response_model=list[GroupOut])
async def list_my_groups(current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)) -> list[GroupOut]:
    groups = await svc.get_user_groups(db, user_id=current_user.id)
    return [await _group_to_out(db, group) for group in groups]


@router.get("/groups/{group_id}", response_model=GroupOut)
async def get_group_detail(
    group_id: str, current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
) -> GroupOut:
    await _require_group_member(db, group_id=group_id, user_id=current_user.id)
    group = await svc.get_group(db, group_id=group_id)
    if group is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Group not found")
    return await _group_to_out(db, group)


@router.patch("/groups/{group_id}", response_model=GroupOut)
async def rename_group(
    group_id: str, payload: GroupRenameRequest, current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
) -> GroupOut:
    await _require_group_member(db, group_id=group_id, user_id=current_user.id)
    group = await svc.rename_group(db, group_id=group_id, new_name=payload.name)
    return await _group_to_out(db, group)


@router.delete("/groups/{group_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_group(
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


def _expense_to_out(expense, splits) -> SharedExpenseOut:
    return SharedExpenseOut(
        id=expense.id,
        group_id=expense.group_id,
        paid_by=expense.paid_by,
        paid_by_name=expense.paid_by_name_snapshot,
        description=expense.description,
        category=expense.category,
        amount=str(expense.amount),
        expense_date=expense.expense_date,
        splits=[SplitOut(user_id=s.user_id, name=s.name_snapshot, share_amount=str(s.share_amount)) for s in splits],
        created_at=expense.created_at,
        updated_at=expense.updated_at,
    )


@router.post("/groups/{group_id}/expenses", response_model=SharedExpenseOut, status_code=status.HTTP_201_CREATED)
async def create_expense(
    group_id: str,
    payload: SharedExpenseCreateRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> SharedExpenseOut:
    await _require_group_member(db, group_id=group_id, user_id=current_user.id)

    group_member_ids = {m.user_id for m in await svc.get_group_members(db, group_id=group_id) if m.user_id}
    invalid = set(payload.participant_ids) - group_member_ids
    if invalid:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="All participants must be members of this group")

    expense = await svc.create_shared_expense(
        db,
        group_id=group_id,
        paid_by=current_user.id,  # always the caller — see module docstring
        description=payload.description,
        amount=_to_decimal(payload.amount, "amount"),
        expense_date=payload.expense_date,
        participant_ids=payload.participant_ids,
        category=payload.category,
    )
    splits = await svc.get_expense_splits(db, expense_id=expense.id)
    return _expense_to_out(expense, splits)


@router.get("/groups/{group_id}/expenses", response_model=list[SharedExpenseOut])
async def list_group_expenses(
    group_id: str, current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
) -> list[SharedExpenseOut]:
    await _require_group_member(db, group_id=group_id, user_id=current_user.id)
    expenses = await svc.get_group_expenses(db, group_id=group_id)
    result = []
    for e in expenses:
        splits = await svc.get_expense_splits(db, expense_id=e.id)
        result.append(_expense_to_out(e, splits))
    return result


async def _require_expense_access(db: AsyncSession, *, expense_id: str, user_id: str):
    expense = await svc.get_expense(db, expense_id=expense_id)
    if expense is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Expense not found")
    await _require_group_member(db, group_id=expense.group_id, user_id=user_id)
    return expense


@router.get("/expenses/{expense_id}", response_model=SharedExpenseOut)
async def get_expense_detail(
    expense_id: str, current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
) -> SharedExpenseOut:
    expense = await _require_expense_access(db, expense_id=expense_id, user_id=current_user.id)
    splits = await svc.get_expense_splits(db, expense_id=expense_id)
    return _expense_to_out(expense, splits)


@router.patch("/expenses/{expense_id}", response_model=SharedExpenseOut)
async def edit_expense(
    expense_id: str,
    payload: SharedExpenseEditRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> SharedExpenseOut:
    await _require_expense_access(db, expense_id=expense_id, user_id=current_user.id)
    new_amount = _to_decimal(payload.amount, "amount") if payload.amount is not None else None
    expense = await svc.edit_shared_expense(
        db, expense_id=expense_id, edited_by=current_user.id, new_amount=new_amount, new_description=payload.description, new_category=payload.category
    )
    splits = await svc.get_expense_splits(db, expense_id=expense_id)
    return _expense_to_out(expense, splits)


@router.get("/expenses/{expense_id}/comments", response_model=list[CommentOut])
async def list_comments(
    expense_id: str, current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
) -> list[CommentOut]:
    await _require_expense_access(db, expense_id=expense_id, user_id=current_user.id)
    comments = await svc.get_expense_comments(db, expense_id=expense_id)
    return [CommentOut(id=c.id, user_id=c.user_id, name=c.name_snapshot, body=c.body, is_system=c.is_system, created_at=c.created_at) for c in comments]


@router.post("/expenses/{expense_id}/comments", response_model=CommentOut, status_code=status.HTTP_201_CREATED)
async def add_comment(
    expense_id: str,
    payload: CommentCreateRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> CommentOut:
    await _require_expense_access(db, expense_id=expense_id, user_id=current_user.id)
    comment = await svc.add_comment(db, expense_id=expense_id, user_id=current_user.id, body=payload.body)
    return CommentOut(id=comment.id, user_id=comment.user_id, name=comment.name_snapshot, body=comment.body, is_system=comment.is_system, created_at=comment.created_at)


@router.post("/settlements", response_model=SettlementOut, status_code=status.HTTP_201_CREATED)
async def record_settlement(
    payload: SettlementCreateRequest, current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
) -> SettlementOut:
    to_user = await user_repository.get_by_id(db, payload.to_user_id)
    if to_user is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="User not found")

    settlement = await svc.record_settlement(
        db,
        from_user_id=current_user.id,
        to_user_id=payload.to_user_id,
        amount=_to_decimal(payload.amount, "amount"),
        settled_date=payload.settled_date,
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


@router.get("/balances", response_model=list[BalanceOut])
async def my_balances(current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)) -> list[BalanceOut]:
    balances = await svc.get_all_balances(db, user_id=current_user.id)
    result = []
    for b in balances:
        balance = b["balance"]  # positive = current_user owes them; negative = they owe current_user
        result.append(BalanceOut(
            user_id=b["user_id"],
            name=b["name"],
            you_owe_them=str(max(balance, Decimal("0.00"))),
            they_owe_you=str(max(-balance, Decimal("0.00"))),
        ))
    return result
