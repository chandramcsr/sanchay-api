from pydantic import BaseModel, Field

VALID_RECORD_TYPES = {
    "will", "nomination", "contract", "financial_dispute",
    "insurance_claim", "property_document", "compliance_deadline",
}


class LegalRecordCreateRequest(BaseModel):
    record_type: str
    title: str = Field(min_length=1, max_length=200)
    status: str | None = Field(default=None, max_length=30)
    key_date: str | None = None  # YYYY-MM-DD
    amount: float | None = Field(default=None, ge=0)
    counterparty: str | None = Field(default=None, max_length=200)
    document_location: str | None = Field(default=None, max_length=300)
    notes: str | None = Field(default=None, max_length=5000)


class LegalRecordUpdateRequest(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    status: str | None = Field(default=None, max_length=30)
    key_date: str | None = None
    amount: float | None = Field(default=None, ge=0)
    counterparty: str | None = Field(default=None, max_length=200)
    document_location: str | None = Field(default=None, max_length=300)
    notes: str | None = Field(default=None, max_length=5000)


class LegalRecordOut(BaseModel):
    id: str
    record_type: str
    title: str
    status: str | None
    key_date: str | None
    amount: float | None
    counterparty: str | None
    document_location: str | None
    notes: str | None
    created_at: str
    updated_at: str
