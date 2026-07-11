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
    avatar_data: str | None = None  # looked up LIVE from the current user record, never snapshotted — see _group_to_out


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
    # Who paid — defaults to the caller if omitted. Can be set to any
    # OTHER real member's user_id (see the router module's own
    # docstring for the deliberate trust tradeoff this represents), OR
    # paid_by_pending can name someone who hasn't signed up yet (same
    # {email, name} shape as pending_participants) — invited to the
    # group the same way a new pending PARTICIPANT already is, and
    # reconnected to their real account automatically if/when they
    # sign up (reconnect_by_email already handles this — it was
    # already correctly written for this case, just never reachable
    # before this field existed to trigger it).
    paid_by: str | None = None
    paid_by_pending: MemberInvite | None = None

    @field_validator("paid_by_pending")
    @classmethod
    def not_both_paid_by_fields(cls, v, info):
        if v is not None and info.data.get("paid_by"):
            raise ValueError("Set either paid_by or paid_by_pending, not both")
        return v

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
    # Same shape and same mutual-exclusivity as the create request —
    # None on both means "leave paid_by exactly as it is," not
    # "reset to the caller"; unlike create, edit has no sensible
    # default to fall back to.
    paid_by: str | None = None
    paid_by_pending: MemberInvite | None = None

    @field_validator("paid_by_pending")
    @classmethod
    def not_both_paid_by_fields(cls, v, info):
        if v is not None and info.data.get("paid_by"):
            raise ValueError("Set either paid_by or paid_by_pending, not both")
        return v


class SplitOut(BaseModel):
    user_id: str | None
    name: str
    share_amount: str  # decimal string, never float — this is money
    avatar_data: str | None = None  # looked up LIVE, never snapshotted — see _expense_to_out


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


# ---------- recurring shared expenses ----------

class RecurringRuleCreateRequest(BaseModel):
    description: str
    amount: float = Field(gt=0)
    category: str = "Other"
    split_type: Literal["equal", "shares", "percentage", "exact"] = "equal"
    participant_ids: list[str] = Field(default_factory=list)
    pending_participants: list[MemberInvite] = Field(default_factory=list)
    participant_values: dict[str, float] = Field(default_factory=dict)
    frequency: Literal["weekly", "biweekly", "monthly", "quarterly", "yearly"]
    start_date: str  # YYYY-MM-DD
    end_date: str | None = None
    # Who pays each materialized occurrence — defaults to the caller
    # if omitted, same as one-off expenses. Stored on the SAME
    # created_by/created_by_name_snapshot fields the rule already had
    # (no new columns): "who set this schedule up" and "who pays each
    # cycle" were always effectively the same field in practice, since
    # materialize_due_shared_expenses already used created_by as the
    # payer for every occurrence — this just makes that explicit and
    # overridable, matching what one-off expenses already allow.
    paid_by: str | None = None
    paid_by_pending: MemberInvite | None = None

    @field_validator("paid_by_pending")
    @classmethod
    def not_both_paid_by_fields(cls, v, info):
        if v is not None and info.data.get("paid_by"):
            raise ValueError("Set either paid_by or paid_by_pending, not both")
        return v

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
        if not info.data.get("participant_ids") and not v:
            raise ValueError("At least one participant is required")
        return v


class RecurringRuleEditRequest(BaseModel):
    """
    All optional, same "None means leave it alone" convention as
    SharedExpenseEditRequest — including for paid_by/paid_by_pending,
    where both None means "don't touch who pays," not "clear it."

    Deliberately has no start_date at all — not just optional-and-
    unused, genuinely absent. The anchor date drives due_occurrences()
    together with last_materialized; changing it after occurrences
    have already been generated would desync the two in ways that are
    hard to reason about (does changing the anchor retroactively
    change what "already materialized" means?). Same restriction the
    personal recurring engine already has for the identical reason —
    edits apply to future occurrences only, never re-anchor the
    schedule itself.
    """
    description: str | None = None
    amount: float | None = Field(default=None, gt=0)
    category: str | None = None
    split_type: Literal["equal", "shares", "percentage", "exact"] | None = None
    participant_ids: list[str] | None = None
    pending_participants: list[MemberInvite] | None = None
    participant_values: dict[str, float] | None = None
    frequency: Literal["weekly", "biweekly", "monthly", "quarterly", "yearly"] | None = None
    end_date: str | None = None
    clear_end_date: bool = False  # end_date=None is ambiguous ("don't touch" vs "remove it") — this disambiguates, same problem SharedExpenseEditRequest's paid_by never had (an expense's payer is never "cleared", just changed) but a schedule's end date genuinely can be removed
    paid_by: str | None = None
    paid_by_pending: MemberInvite | None = None

    @field_validator("paid_by_pending")
    @classmethod
    def not_both_paid_by_fields(cls, v, info):
        if v is not None and info.data.get("paid_by"):
            raise ValueError("Set either paid_by or paid_by_pending, not both")
        return v

    @field_validator("description")
    @classmethod
    def description_not_blank_if_given(cls, v: str | None) -> str | None:
        if v is not None:
            v = v.strip()
            if not v:
                raise ValueError("Description can't be blank")
        return v


class RecurringRuleOut(BaseModel):
    id: str
    group_id: str
    created_by: str | None
    created_by_name: str
    description: str
    amount: str
    category: str
    split_type: str
    participant_ids: list[str]
    pending_participants: list[MemberInvite]
    # Keyed the same way SharedExpenseOut's splits imply — real
    # user_id or normalized pending email — string values (not float)
    # for the same reason every other money-adjacent value in this
    # file is a string: floats have no business representing exact
    # decimal amounts, and this can hold exact dollar amounts (split_
    # type "exact") as well as shares/percentages.
    participant_values: dict[str, str]
    frequency: str
    start_date: str
    end_date: str | None
    last_materialized: str | None
    active: bool
    created_at: datetime


class SetRecurringRuleActiveRequest(BaseModel):
    active: bool
