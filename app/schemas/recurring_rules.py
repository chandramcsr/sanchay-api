from pydantic import BaseModel, Field

VALID_FREQUENCIES = {"weekly", "biweekly", "monthly", "quarterly", "yearly"}


class RecurringRuleCreateRequest(BaseModel):
    account_id: str
    amount: float  # signed: positive = income/credit, negative = expense/debit
    description: str = Field(min_length=1, max_length=512)
    category: str | None = Field(default=None, max_length=128)
    frequency: str
    start_date: str  # YYYY-MM-DD
    end_date: str | None = None


class RecurringRuleUpdateRequest(BaseModel):
    amount: float | None = None
    description: str | None = Field(default=None, min_length=1, max_length=512)
    category: str | None = Field(default=None, max_length=128)
    frequency: str | None = None
    end_date: str | None = None


class RecurringRuleOut(BaseModel):
    id: str
    account_id: str
    amount: float
    description: str
    category: str | None
    frequency: str
    start_date: str
    end_date: str | None
    last_materialized: str | None
    created_at: str
    updated_at: str | None
