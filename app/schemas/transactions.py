from pydantic import BaseModel, Field


class TransactionCreateRequest(BaseModel):
    account_id: str
    amount: float  # signed: positive = income/credit, negative = expense/debit
    description: str | None = Field(default=None, max_length=512)
    category: str | None = Field(default=None, max_length=128)
    date: str  # YYYY-MM-DD


class TransactionUpdateRequest(BaseModel):
    amount: float | None = None
    description: str | None = Field(default=None, min_length=1, max_length=512)
    category: str | None = Field(default=None, max_length=128)
    date: str | None = None


class TransactionOut(BaseModel):
    id: str
    account_id: str
    amount: float
    description: str | None
    category: str | None
    date: str
    created_at: str
    updated_at: str | None
