"""Redis commands used by the job queue and rate limiter, plus an in-process stand-in."""

import os


class MemoryRedis:
    """Same command subset as redis.Redis(decode_responses=True) for local tests."""

    def __init__(self):
        self.hashes: dict[str, dict] = {}
        self.lists: dict[str, list] = {}
        self.counters: dict[str, int] = {}
        self.store: dict[str, str] = {}

    def hset(self, key, mapping):
        bucket = self.hashes.setdefault(key, {})
        bucket.update({field: "" if value is None else str(value) for field, value in mapping.items()})
        return len(mapping)

    def hgetall(self, key):
        return dict(self.hashes.get(key, {}))

    def lpush(self, key, value):
        items = self.lists.setdefault(key, [])
        items.insert(0, str(value))
        return len(items)

    def brpop(self, key, timeout=0):
        items = self.lists.get(key) or []
        if not items:
            return None
        return (key, items.pop())

    def incr(self, key):
        self.counters[key] = self.counters.get(key, 0) + 1
        return self.counters[key]

    def expire(self, key, seconds):
        return True

    def get(self, key):
        if key in self.store:
            return self.store[key]
        return None

    def set(self, key, value, ex=None):
        # ponytail: in-process stand-in does not evict on `ex`. Real Redis does.
        self.store[key] = "" if value is None else str(value)
        return True


def build_client():
    url = os.environ.get("REDIS_URL", "").strip()
    if not url:
        return MemoryRedis()
    import redis

    # BRPOP in the worker waits 5s. The read timeout has to be longer or that wait kills the process.
    return redis.Redis.from_url(
        url,
        decode_responses=True,
        socket_connect_timeout=2,
        socket_timeout=30,
    )
