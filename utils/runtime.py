"""Process-wide queue and limiter. Configured when the API starts."""

import os

from utils.jobs import JobQueue
from utils.ratelimit import RateLimiter
from utils.redis_client import build_client

_jobs: JobQueue | None = None
_limiter: RateLimiter | None = None


def configure(jobs: JobQueue, limiter: RateLimiter) -> None:
    global _jobs, _limiter
    _jobs = jobs
    _limiter = limiter


def build_backends() -> tuple[JobQueue, RateLimiter]:
    client = build_client()
    jobs = JobQueue(client)
    limiter = RateLimiter(
        client,
        user_limit=int(os.environ.get("RATE_LIMIT_USER", "60")),
        tenant_limit=int(os.environ.get("RATE_LIMIT_TENANT", "300")),
        window=int(os.environ.get("RATE_LIMIT_WINDOW_SECONDS", "60")),
    )
    return jobs, limiter


def get_backends() -> tuple[JobQueue, RateLimiter]:
    global _jobs, _limiter
    if _jobs is None or _limiter is None:
        configure(*build_backends())
    return _jobs, _limiter


def enqueue_etl(*, question: str, user_id: str, tenant_id: str, role: str) -> str:
    jobs, _ = get_backends()
    return jobs.enqueue(question=question, user_id=user_id, tenant_id=tenant_id, role=role)
