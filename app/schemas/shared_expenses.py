from datetime import datetime
from typing import Literal

from pydantic import BaseModel, EmailStr, Field, field_validator


# ---------- groups ----------

class MemberInvite(BaseModel):
    """
    email is always required. name is only actually USED if this
    email turns out to have no Sanchay account yet (an existing
    account's own real display name is used instead) — but asking for
    it upfront, every time, means a not-yet-registered person is
    never just a bare email address anywhere in the app; "Sam" shows
    up instead of "sam.usajobs@gmail.com" the moment they're added,
    not after they eventually sign up.
    """
    email: EmailStr
    name: str = ""


class GroupCreateRequest(BaseModel):
    name: str
    members: list[MemberInvite] = Field(default_factory=list)

    @field_validator("name")
    @classmethod
    def name_not_blank(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("Group name is required")
        return v


class AddMemberRequest(MemberInvite):
    pass


class GroupRenameRequest(BaseModel):
    name: str

    @field_validator("name")
    @classmethod
    def name_not_blank(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("Group name is required")
        return v


class GroupMemberOut(BaseModel):
    user_id: str | None  # None if this member's account has since been deleted (frozen)
    name: str  # never the member's email — no group member ever sees another's email address


class PendingInviteOut(BaseModel):
    name: str
    email: str


class GroupOut(BaseModel):
    id: str
    name: str
    members: list[GroupMemberOut]
    pending_invites: list[PendingInviteOut]  # people invited but who haven't signed up yet
    created_at: datetime


# ---------- shared expenses ----------

class SharedExpenseCreateRequest(BaseModel):
    description: str
    amount: float = Field(gt=0)
    expense_date: str  # YYYY-MM-DD
    participant_ids: list[str] = Field(default_factory=list)
    pending_participants: list[MemberInvite] = Field(default_factory=list)
    category: str = "Other"
    split_type: Literal["equal", "shares", "percentage", "exact"] = "equal"
    # Keyed by user_id for real participants, or the pending
    # participant's normalized (lowercase) email — only read when
    # split_type != "equal". Value meaning depends on split_type:
    # a share count, a percentage (0-100), or an exact dollar amount.
    participant_values: dict[str, float] = Field(default_factory=dict)

    @field_validator("description")
    @classmethod
    def description_not_blank(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("Description is required")
        return v

    @field_validator("pending_participants")
    @classmethod
    def at_least_one_participant_somewhere(cls, v, info):
        # participant_ids is validated first (field order above), so
        # info.data has it available here to check the COMBINED total
        # rather than requiring either list alone to be non-empty.
        if not v and not info.data.get("participant_ids"):
            raise ValueError("At least one participant is required")
        return v


class SharedExpenseEditRequest(BaseModel):
    amount: float | None = Field(default=None, gt=0)
    description: str | None = None
    category: str | None = None
    expense_date: str | None = None
    participant_ids: list[str] | None = None
    pending_participants: list[MemberInvite] | None = None
    split_type: Literal["equal", "shares", "percentage", "exact"] | None = None
    participant_values: dict[str, float] | None = None


class SplitOut(BaseModel):
    user_id: str | None
    name: str
    share_amount: str  # decimal string, never float — this is money


class SharedExpenseOut(BaseModel):
    id: str
    group_id: str
    paid_by: str | None
    paid_by_name: str
    description: str
    category: str
    split_type: str
    amount: str
    expense_date: str
    splits: list[SplitOut]
    created_at: datetime
    updated_at: datetime


class CommentCreateRequest(BaseModel):
    body: str

    @field_validator("body")
    @classmethod
    def body_not_blank(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("Comment can't be empty")
        return v


class CommentOut(BaseModel):
    id: str
    user_id: str | None
    name: str
    body: str
    is_system: bool
    created_at: datetime


# ---------- settlements ----------

class SettlementCreateRequest(BaseModel):
    to_user_id: str
    amount: float = Field(gt=0)
    settled_date: str  # YYYY-MM-DD


class SettlementOut(BaseModel):
    id: str
    from_user_id: str | None
    from_name: str
    to_user_id: str | None
    to_name: str
    amount: str
    settled_date: str


# ---------- balances ----------

class BalanceOut(BaseModel):
    """
    Deliberately two separate non-negative fields, not one signed
    number — exactly one is ever non-zero. A real sign-confusion bug
    was found and fixed elsewhere in this app recently (credit card
    debt counted as an asset); this shape makes the equivalent
    mistake structurally impossible to make when building whatever
    reads this response.
    """
    user_id: str
    name: str
    you_owe_them: str  # decimal string, "0.00" if not applicable
    they_owe_you: str  # decimal string, "0.00" if not applicable
