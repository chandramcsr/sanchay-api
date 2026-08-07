from pydantic import BaseModel, Field


class AskRequest(BaseModel):
    question: str = Field(min_length=1, max_length=1000)


class AskSourceOut(BaseModel):
    label: str
    text: str
    similarity: float


class AskResponseOut(BaseModel):
    answer: str
    sources: list[AskSourceOut]
    abstained: bool
    grounded: bool
