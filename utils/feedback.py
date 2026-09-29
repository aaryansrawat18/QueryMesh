"""Thumbs on a previous request. Append-only, same idea as the audit log."""

import json
import os
import time
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]


def feedback_path() -> Path:
    raw = os.environ.get("FEEDBACK_LOG_PATH", "").strip()
    if raw:
        return Path(raw)
    return _ROOT / "data" / "feedback.jsonl"


def store(
    *,
    request_id: str,
    user_id: str,
    tenant_id: str,
    rating: str,
    comment: str,
    question: str,
) -> None:
    row = {
        "ts": time.time(),
        "request_id": request_id,
        "user_id": user_id,
        "tenant_id": tenant_id,
        "rating": rating,
        "comment": comment,
        "question": question,
    }
    path = feedback_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row) + "\n")


def load_rows(path: Path | None = None) -> list[dict]:
    target = path or feedback_path()
    if not target.is_file():
        return []
    rows = []
    for line in target.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def uncovered_questions(cases: list[dict], rows: list[dict]) -> int:
    """Down votes whose question is not already in the golden set."""
    known = {(case.get("question") or "").strip().lower() for case in cases}
    count = 0
    for row in rows:
        if row.get("rating") != "down":
            continue
        question = (row.get("question") or "").strip().lower()
        if question and question not in known:
            count += 1
    return count


def prompt_notes(rows: list[dict]) -> str:
    """Text to attach the next time prompts are revised. Up votes are ignored."""
    lines = []
    for row in rows:
        if row.get("rating") != "down":
            continue
        question = (row.get("question") or "").strip()
        comment = (row.get("comment") or "").strip()
        if not question and not comment:
            continue
        lines.append(f"- question: {question} comment: {comment}")
    if not lines:
        return ""
    return "Revise prompts using thumbs-down feedback:\n" + "\n".join(lines)
