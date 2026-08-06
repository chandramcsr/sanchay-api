from pydantic import BaseModel, Field


class BudgetUpsertRequest(BaseModel):
    category: str = Field(min_length=1, max_length=128)
    monthly_limit: float = Field(gt=0)


class BudgetOut(BaseModel):
    id: str
    category: str
    monthly_limit: float
    spent: float
    created_at: str
    updated_at: str | None
