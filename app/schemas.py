from typing import List, Optional, Literal
from pydantic import BaseModel, Field, ConfigDict


class Principal(BaseModel):
    user_id: str
    company_id: str
    role: Literal["employee", "admin"]
    display_name: str


class TokenRequest(BaseModel):
    username: str = Field(..., min_length=1, max_length=100)
    password: str = Field(..., min_length=1, max_length=100)


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int
    company_id: str
    role: str
    display_name: str


class AskRequest(BaseModel):
    question: str = Field(..., min_length=1, max_length=1000)

    # Strictly forbid any client-supplied tenant, version, or collection overrides
    model_config = ConfigDict(extra="forbid")


class Citation(BaseModel):
    document_title: str
    version: int
    section_id: str
    section_title: str
    chunk_id: str
    excerpt: str


class TimingMs(BaseModel):
    retrieval: float
    llm: float
    total: float


class AskResponse(BaseModel):
    request_id: str
    status: Literal["answered", "not_covered"]
    answer: str
    citations: List[Citation] = Field(default_factory=list)
    timing_ms: TimingMs


class ErrorResponse(BaseModel):
    request_id: str
    status: str = "retryable_error"
    message: str


class PolicyVersionSummary(BaseModel):
    id: str
    company_id: str
    version: int
    title: str
    sha256: str
    collection_name: str
    status: str
    created_at: str
    activated_at: Optional[str] = None
    is_active: bool = False
    chunk_count: Optional[int] = None


class ActivePolicyResponse(BaseModel):
    company_id: str
    policy_version_id: str
    version: int
    title: str
    collection_name: str
    activated_at: Optional[str] = None


class PolicyUploadResponse(BaseModel):
    message: str
    policy_id: str
    version: int
    status: str
    chunk_count: int
