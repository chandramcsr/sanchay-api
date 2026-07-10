from datetime import datetime

from pydantic import BaseModel, EmailStr, Field, field_validator


# ---------- groups ----------

class GroupCreateRequest(BaseModel):
    name: str
    member_emails: list[EmailStr] = Field(default_factory=list)

    @field_validator("name")
    @classmethod
    def name_not_blank(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("Group name is required")
        return v


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


class GroupOut(BaseModel):
    id: str
    name: str
    members: list[GroupMemberOut]
    pending_invites: list[str]  # emails of people invited but who haven't signed up yet
    created_at: datetime


# ---------- shared expenses ----------

class SharedExpenseCreateRequest(BaseModel):
    description: str
    amount: float = Field(gt=0)
    expense_date: str  # YYYY-MM-DD
    participant_ids: list[str] = Field(min_length=1)
    category: str = "Other"

    @field_validator("description")
    @classmethod
    def description_not_blank(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("Description is required")
        return v


class SharedExpenseEditRequest(BaseModel):
    amount: float | None = Field(default=None, gt=0)
    description: str | None = None
    category: str | None = None


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
