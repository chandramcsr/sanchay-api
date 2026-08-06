from pydantic import BaseModel, Field


class SavingsGoalCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=256)
    target_amount: float = Field(gt=0)
    target_date: str | None = None  # YYYY-MM-DD, optional
    account_id: str


class SavingsGoalUpdateRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=256)
    target_amount: float | None = Field(default=None, gt=0)
    target_date: str | None = None
    # account_id IS editable here (unlike transactions/recurring rules)
    # -- matches ledger-app's own GoalModal, which lets you re-point a
    # goal at a different account on edit, not just at creation. A
    # goal's progress is entirely derived from whichever account it
    # currently points at, so there's no historical data tied to the
    # old account that repointing would orphan or invalidate.
    account_id: str | None = None


class SavingsGoalOut(BaseModel):
    id: str
    name: str
    target_amount: float
    target_date: str | None
    account_id: str
    # Computed progress fields -- derived from the linked account's
    # current balance and its recent transaction history, not stored
    # columns. See savings_goal_service.py's goal_progress().
    current_amount: float
    pct: float
    remaining: float
    monthly_contribution_rate: float
    projected_completion_date: str | None
    on_track_for_target_date: bool | None
    created_at: str
    updated_at: str | None
