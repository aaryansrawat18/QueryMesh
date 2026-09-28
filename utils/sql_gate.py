"""Deterministic read-only SQL check. The LLM judge is not this boundary."""

import os
import re

from api.policy import COLUMNS, _RANK

_FORBIDDEN = re.compile(
    r"\b(insert|update|delete|drop|alter|truncate|create|grant|revoke|copy|execute|call|comment|merge|vacuum)\b",
    re.I,
)


def normalize_sql(query: str) -> str:
    text = (query or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
        text = text.strip()
    return text


def assert_read_only(query: str) -> str:
    text = normalize_sql(query)
    stripped = re.sub(r"/\*.*?\*/", " ", text, flags=re.S)
    stripped = re.sub(r"--[^\n]*", " ", stripped)
    parts = [part.strip() for part in stripped.split(";") if part.strip()]
    if len(parts) != 1:
        raise ValueError("multiple statements require approval")
    statement = parts[0]
    if _FORBIDDEN.search(statement):
        raise ValueError("write requires approval")
    if not re.match(r"(select|with)\b", statement, re.I):
        raise ValueError("only SELECT or WITH is allowed")
    return statement


def high_risk(query: str) -> str | None:
    """Sensitive columns and unbounded reads wait for a person. Writes are not auto-run."""
    for key, level in COLUMNS.items():
        if _RANK[level] < 2:
            continue
        column = key.split(".", 1)[1]
        if re.search(rf"\b{re.escape(column)}\b", query, re.I):
            return f"sensitive column {key}"
    limit = re.search(r"\blimit\s+(\d+)\b", query, re.I)
    cap = int(os.environ.get("SQL_APPROVAL_LIMIT", "1000"))
    if limit is None:
        return "query has no row limit"
    if int(limit.group(1)) > cap:
        return "large scan"
    return None
