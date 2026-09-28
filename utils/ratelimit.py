"""Fixed-window rate limit per user and per tenant."""

import time


class RateLimiter:
    def __init__(self, client, user_limit: int, tenant_limit: int, window: int):
        self.client = client
        self.user_limit = user_limit
        self.tenant_limit = tenant_limit
        self.window = window

    def allow(self, user_id: str, tenant_id: str, now: float | None = None) -> bool:
        bucket = int((time.time() if now is None else now) // self.window)
        checks = (
            (f"qm:rl:user:{user_id}:{bucket}", self.user_limit),
            (f"qm:rl:tenant:{tenant_id}:{bucket}", self.tenant_limit),
        )
        allowed = True
        for key, limit in checks:
            count = int(self.client.incr(key))
            if count == 1:
                self.client.expire(key, self.window)
            if count > limit:
                allowed = False
        return allowed
