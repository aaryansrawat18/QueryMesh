"""Retries, backoff, and a process-local circuit breaker for LLM and database calls."""

import os
import time

from utils.budget import BudgetExceeded


class CircuitOpen(RuntimeError):
    def __init__(self, name: str):
        super().__init__(f"{name} unavailable")
        self.name = name


class Breaker:
    # ponytail: counts live in this process. Share them in Redis when you run more than one API worker.
    def __init__(self, name: str):
        self.name = name
        self.failures = 0
        self.open_until = 0.0

    def reset(self) -> None:
        self.failures = 0
        self.open_until = 0.0

    def allow(self) -> None:
        if time.time() < self.open_until:
            raise CircuitOpen(self.name)

    def success(self) -> None:
        self.failures = 0

    def failure(self) -> None:
        self.failures += 1
        limit = int(os.environ.get("CIRCUIT_FAILURES", "5"))
        if self.failures >= limit:
            self.open_until = time.time() + float(os.environ.get("CIRCUIT_COOLDOWN_SECONDS", "30"))


llm_breaker = Breaker("llm")
db_breaker = Breaker("db")


def llm_retryable(exc: BaseException) -> bool:
    if isinstance(exc, (BudgetExceeded, CircuitOpen, ValueError)):
        return False
    if isinstance(exc, (TimeoutError, ConnectionError, OSError)):
        return True
    name = type(exc).__name__
    return any(token in name for token in ("Timeout", "Connection", "RateLimit", "APIError", "ServiceUnavailable"))


def call(breaker: Breaker, fn, *, attempts: int = 2, retryable=None):
    """Run fn. Retry retryable errors with exponential backoff. Open the breaker after the last miss."""
    breaker.allow()
    delay = float(os.environ.get("RETRY_BASE_SECONDS", "0.2"))
    check = retryable or llm_retryable
    last = None
    tries = max(1, attempts)
    for attempt in range(tries):
        try:
            value = fn()
        except CircuitOpen:
            raise
        except Exception as exc:
            if not check(exc):
                raise
            last = exc
            if attempt + 1 == tries:
                breaker.failure()
                raise
            time.sleep(delay * (2**attempt))
            continue
        breaker.success()
        return value
    raise last
