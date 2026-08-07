from pydantic import BaseModel, Field


class DiscussionActionItemSchema(BaseModel):
    owner: str
    task: str


class DiscussionAnalysisSchema(BaseModel):
    summary: list[str]
    decisions: list[str]
    actions: list[DiscussionActionItemSchema]
    risks: list[str]
    suggestions: list[str]
    questions: list[str]
    sentiment: dict[str, int]


class DiscussionCreateRequest(BaseModel):
    title: str = Field(min_length=1, max_length=256)
    transcript: str = Field(min_length=1)
    analysis: DiscussionAnalysisSchema | None = None
    duration_seconds: int = Field(ge=0, default=0)


class DiscussionUpdateRequest(BaseModel):
    # Rename only -- matches ledger-app's DiscussionHistoryModal, which
    # only ever edits the title (transcript/analysis are fixed once a
    # discussion is saved, not editable after the fact).
    title: str = Field(min_length=1, max_length=256)


class DiscussionOut(BaseModel):
    id: str
    title: str
    transcript: str
    analysis: DiscussionAnalysisSchema | None
    duration_seconds: int
    created_at: str
    updated_at: str | None


class DiscussionListItemOut(BaseModel):
    # List view omits the (potentially large) transcript and full
    # analysis -- matches ledger-app's history list, which shows title/
    # date/duration and only loads full detail when a row is expanded.
    id: str
    title: str
    duration_seconds: int
    created_at: str
