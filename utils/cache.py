"""Short-lived cache for identical questions. Fail open if Redis is down."""

import hashlib
import json
import os


def _client():
    from utils.runtime import get_backends

    jobs, _limiter = get_backends()
    return jobs.client


def _key(tenant_id: str, role: str, question: str) -> str:
    raw = f"{tenant_id}|{role}|{question.strip()}"
    return "qm:answer:" + hashlib.sha256(raw.encode()).hexdigest()


def get_answer(tenant_id: str, role: str, question: str) -> dict | None:
    try:
        raw = _client().get(_key(tenant_id, role, question))
    except Exception:
        return None
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if "answer" in data and "route" in data:
        return data
    return None


def put_answer(tenant_id: str, role: str, question: str, payload: dict) -> None:
    ttl = int(os.environ.get("ANSWER_CACHE_TTL", "60"))
    if ttl <= 0:
        return
    try:
        _client().set(_key(tenant_id, role, question), json.dumps(payload), ex=ttl)
    except Exception:
        return
