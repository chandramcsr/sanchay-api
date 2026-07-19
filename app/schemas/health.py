from pydantic import BaseModel, Field


class HealthProfileUpsertRequest(BaseModel):
    height_cm: float | None = Field(default=None, gt=0)
    age: int | None = Field(default=None, gt=0, lt=130)
    biological_sex: str | None = None  # "male" | "female" | "other" | "prefer_not_to_say"
    notes: str | None = Field(default=None, max_length=5000)


class HealthProfileOut(BaseModel):
    height_cm: float | None
    age: int | None
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


class BloodPressureEntryCreateRequest(BaseModel):
    # Bounds are sanity-checking against an obvious data-entry error
    # (a typo, a misplaced digit), not a clinical judgment about what
    # counts as a normal or concerning reading -- deliberately wide.
    systolic: int = Field(gt=40, lt=300)
    diastolic: int = Field(gt=20, lt=200)
    pulse: int | None = Field(default=None, gt=20, lt=250)
    recorded_date: str  # YYYY-MM-DD


class BloodPressureEntryOut(BaseModel):
    id: str
    systolic: int
    diastolic: int
    pulse: int | None
    recorded_date: str
    created_at: str
