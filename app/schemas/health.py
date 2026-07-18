from pydantic import BaseModel, Field


class HealthProfileUpsertRequest(BaseModel):
    height_cm: float | None = Field(default=None, gt=0)
    date_of_birth: str | None = None  # YYYY-MM-DD
    biological_sex: str | None = None  # "male" | "female" | "other" | "prefer_not_to_say"
    notes: str | None = Field(default=None, max_length=5000)


class HealthProfileOut(BaseModel):
    height_cm: float | None
    date_of_birth: str | None
    biological_sex: str | None
    notes: str | None
    updated_at: str


class WeightEntryCreateRequest(BaseModel):
    weight_kg: float = Field(gt=0)
    recorded_date: str  # YYYY-MM-DD


class WeightEntryOut(BaseModel):
    id: str
    weight_kg: float
    recorded_date: str
    created_at: str
