from pydantic import BaseModel, Field


class FeedbackCreateRequest(BaseModel):
    category: str  # "bug" | "idea" | "general"
    message: str = Field(min_length=1, max_length=5000)
    app_version: str | None = None


class FeedbackOut(BaseModel):
    id: str
    category: str
    message: str
    app_version: str | None
    created_at: str
