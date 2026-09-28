import json
import logging
import os
import re
import time
import uuid

import psycopg2
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from api.routes import router
from api.schemas import ErrorResponse, HealthResponse, ReadyResponse

logger = logging.getLogger("querymesh.api")
_REQUEST_ID = re.compile(r"^[A-Za-z0-9._-]{1,128}$")


def _request_id(header: str | None) -> str:
    if header and _REQUEST_ID.fullmatch(header):
        return header
    return str(uuid.uuid4())


class RequestContextMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        request_id = _request_id(request.headers.get("x-request-id"))
        request.state.request_id = request_id
        started = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            logger.exception(json.dumps({"request_id": request_id, "route": request.url.path, "event": "unhandled"}))
            response = JSONResponse(
                status_code=500,
                content=ErrorResponse(error="internal_error", detail="Agent request failed").model_dump(),
            )
        latency_ms = round((time.perf_counter() - started) * 1000, 2)
        response.headers["X-Request-Id"] = request_id
        logger.info(
            json.dumps(
                {
                    "request_id": request_id,
                    "route": request.url.path,
                    "status": response.status_code,
                    "latency_ms": latency_ms,
                }
            )
        )
        return response


def postgres_reachable() -> bool:
    load_dotenv()
    try:
        connection = psycopg2.connect(
            host=os.environ.get("host", "localhost"),
            port=os.environ.get("port", "5432"),
            user=os.environ.get("user", "postgres"),
            password=os.environ.get("password", ""),
            dbname=os.environ.get("database", "postgres"),
            connect_timeout=3,
        )
    except Exception:
        return False
    connection.close()
    return True


def create_app() -> FastAPI:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    app = FastAPI(title="QueryMesh", version="0.1.0")
    app.add_middleware(RequestContextMiddleware)
    app.include_router(router)

    @app.exception_handler(RequestValidationError)
    async def validation_error(_request: Request, exc: RequestValidationError):
        body = ErrorResponse(error="validation_error", detail=str(exc.errors()))
        return JSONResponse(status_code=422, content=body.model_dump())

    @app.exception_handler(HTTPException)
    async def http_error(_request: Request, exc: HTTPException):
        detail = exc.detail if isinstance(exc.detail, str) else str(exc.detail)
        body = ErrorResponse(error="request_failed", detail=detail)
        return JSONResponse(status_code=exc.status_code, content=body.model_dump())

    @app.get("/health", response_model=HealthResponse, tags=["ops"])
    def health() -> HealthResponse:
        return HealthResponse(status="ok")

    @app.get(
        "/ready",
        response_model=ReadyResponse,
        responses={503: {"model": ReadyResponse}},
        tags=["ops"],
    )
    def ready():
        if postgres_reachable():
            return ReadyResponse(status="ready", database="up")
        body = ReadyResponse(status="not_ready", database="down")
        return JSONResponse(status_code=503, content=body.model_dump())

    return app


app = create_app()
