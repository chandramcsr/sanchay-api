from pydantic import BaseModel


class SyncPushRequest(BaseModel):
    ciphertext: str
    encryption_meta: str
    based_on_version: int  # 0 means "no prior version seen — first push"


class SyncPullResponse(BaseModel):
    ciphertext: str
    encryption_meta: str
    version: int


class SyncStatusResponse(BaseModel):
    exists: bool
    version: int | None = None
    updated_at: str | None = None
