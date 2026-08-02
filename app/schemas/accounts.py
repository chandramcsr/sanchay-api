from pydantic import BaseModel, Field

VALID_ACCOUNT_TYPES = {"checking", "savings", "credit_card", "loan", "investment"}


class AccountCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=256)
    type: str
    starting_balance: float = 0
    currency: str = Field(default="USD", min_length=3, max_length=3)


class AccountUpdateRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=256)
    type: str | None = None
    starting_balance: float | None = None
    currency: str | None = Field(default=None, min_length=3, max_length=3)


class AccountOut(BaseModel):
    id: str
    name: str
    type: str
    starting_balance: float
    current_balance: float
    currency: str
    created_at: str
    updated_at: str | None
