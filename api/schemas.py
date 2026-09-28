from typing import Literal

from pydantic import BaseModel, Field


class QueryRequest(BaseModel):
    question: str = Field(..., min_length=1, max_length=4000)


class QueryResponse(BaseModel):
    answer: str
    route: Literal["sql", "etl"]


class ErrorResponse(BaseModel):
    error: str
    detail: str


class HealthResponse(BaseModel):
    status: Literal["ok"]


class ReadyResponse(BaseModel):
    status: Literal["ready", "not_ready"]
    database: Literal["up", "down"]
