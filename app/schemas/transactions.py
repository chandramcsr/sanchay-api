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
    transfer_group_id: str | None
    created_at: str
    updated_at: str | None


class TransferCreateRequest(BaseModel):
    """
    Matches ledger-app's TransferInput exactly. amount is always a
    positive dollar figure here -- the sign convention that expense
    legs are negative and income legs are positive is applied
    server-side when the two transactions are actually built, not
    something the caller decides.
    """

    from_account_id: str
    to_account_id: str
    amount: float = Field(gt=0)
    date: str  # YYYY-MM-DD
    note: str | None = Field(default=None, max_length=512)


class TransferOut(BaseModel):
    """Both legs, so the caller can immediately show what actually
    got created without a follow-up list call."""

    from_transaction: TransactionOut
    to_transaction: TransactionOut
