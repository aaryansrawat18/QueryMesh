"""Score a checked-in question set. No model and no database calls."""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from utils.sql_gate import assert_read_only

SET = Path(__file__).with_name("questions.jsonl")


def judge(case: dict) -> dict:
    route_ok = case.get("route") == case.get("expected_route")
    sql_ok = True
    sql = ""
    if case.get("expected_route") == "sql":
        try:
            sql = assert_read_only(case.get("sql") or "")
        except ValueError:
            sql_ok = False
        for table in case.get("tables") or []:
            if table.lower() not in sql.lower():
                sql_ok = False
    fact = (case.get("fact") or "").lower()
    grounded = True if not fact else fact in (case.get("answer") or "").lower()
    return {"route_ok": route_ok, "sql_ok": sql_ok, "grounded": grounded}


def summarize(cases: list[dict]) -> str:
    rows = [judge(case) for case in cases]
    count = len(rows) or 1
    route = sum(row["route_ok"] for row in rows) / count
    sql = sum(row["sql_ok"] for row in rows) / count
    grounded = sum(row["grounded"] for row in rows) / count
    score = (route + sql + grounded) / 3
    latency = sorted(float(case.get("latency_ms") or 0) for case in cases)
    p50 = latency[len(latency) // 2] if latency else 0
    cost = sum(float(case.get("cost_usd") or 0) for case in cases)
    return (
        f"cases={len(rows)} route={route:.3f} sql={sql:.3f} grounded={grounded:.3f} "
        f"latency_ms_p50={p50:.0f} cost_usd={cost:.4f} score={score:.3f}"
    )


def load() -> list[dict]:
    cases = []
    for line in SET.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            cases.append(json.loads(line))
    return cases


def main() -> str:
    text = summarize(load())
    print(text)
    return text


if __name__ == "__main__":
    summary = main()
    score = float(summary.rsplit("score=", 1)[-1])
    sys.exit(0 if score >= 0.9 else 1)
