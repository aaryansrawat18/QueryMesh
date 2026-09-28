"""Append-only audit log. Who asked what, which SQL ran, and how it ended."""

import json
import logging
import os
import time
from pathlib import Path

logger = logging.getLogger("querymesh.audit")
_ROOT = Path(__file__).resolve().parents[1]


def audit_path() -> Path:
    raw = os.environ.get("AUDIT_LOG_PATH", "").strip()
    if raw:
        return Path(raw)
    # ponytail: local JSONL. Move to an append-only table when there is a system of record.
    return _ROOT / "data" / "audit.jsonl"


def record(
    *,
    request_id: str,
    user_id: str,
    tenant_id: str,
    question: str,
    sql: str = "",
    tools: list | None = None,
    status: str,
) -> None:
    row = {
        "ts": time.time(),
        "request_id": request_id,
        "user_id": user_id,
        "tenant_id": tenant_id,
        "question": question,
        "sql": sql,
        "tools": list(tools or []),
        "status": status,
    }
    try:
        path = audit_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, default=str) + "\n")
    except Exception:
        logger.exception("audit write failed")
