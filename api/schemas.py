from typing import Literal

from pydantic import BaseModel, Field


class QueryRequest(BaseModel):
    question: str = Field(..., min_length=1, max_length=4000)


class QueryResponse(BaseModel):
    answer: str
    route: Literal["sql", "etl"]
    needs_approval: bool = False


class ErrorResponse(BaseModel):
    error: str
    detail: str


class HealthResponse(BaseModel):
    status: Literal["ok"]


class ReadyResponse(BaseModel):
    status: Literal["ready", "not_ready"]
    database: Literal["up", "down"]


class JobCreate(BaseModel):
    question: str = Field(..., min_length=1, max_length=4000)


class JobCreated(BaseModel):
    job_id: str
    status: Literal["queued"]


class JobView(BaseModel):
    job_id: str
    status: Literal["queued", "running", "succeeded", "failed"]
    result: str = ""
    error: str = ""


class FeedbackRequest(BaseModel):
    request_id: str = Field(..., min_length=1, max_length=128)
    rating: Literal["up", "down"]
    comment: str = Field(default="", max_length=2000)


class FeedbackResponse(BaseModel):
    request_id: str
    rating: Literal["up", "down"]
    stored: Literal[True] = True
