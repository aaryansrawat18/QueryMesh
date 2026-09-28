"""Phase 5: traces, metrics, audit, retries, and the offline eval."""

import json
import os
import time
from pathlib import Path

import jwt
import psycopg2
import pytest

os.environ.setdefault("JWT_SECRET", "test-secret-at-least-32-bytes-long")
os.environ["RATE_LIMIT_USER"] = "100000"
os.environ["RATE_LIMIT_TENANT"] = "100000"
os.environ["RETRY_BASE_SECONDS"] = "0"

import api.app as app_module
import api.routes as routes
from api.policy import assert_can
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage
from Models.schema import JudgeSchema, RouterSchema
from utils.audit import record
from utils.budget import BudgetExceeded, Metered, begin
from utils.database import DatabaseUtil
from utils.guard import assert_url_allowed, jail_path
from utils.jobs import JobQueue
from utils.metrics import metrics
from utils.redis_client import MemoryRedis
from utils.reliability import Breaker, CircuitOpen, call, db_breaker, llm_breaker
from utils.runtime import configure, get_backends
from utils.sql_gate import assert_read_only
from utils.trace import recent, set_request_id, span

client = TestClient(app_module.app)
ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def _reset_breakers():
    llm_breaker.reset()
    db_breaker.reset()
    yield
    llm_breaker.reset()
    db_breaker.reset()


def bearer(role="analyst", tenant_id="acme", user_id="user_42"):
    payload = {
        "sub": user_id,
        "role": role,
        "tenant_id": tenant_id,
        "exp": int(time.time()) + 3600,
    }
    token = jwt.encode(payload, os.environ["JWT_SECRET"], algorithm="HS256")
    return {"Authorization": f"Bearer {token}"}


def test_sql_gate_rejects_dml_and_stacked_statements():
    with pytest.raises(ValueError):
        assert_read_only("DROP TABLE users")
    with pytest.raises(ValueError):
        assert_read_only("SELECT 1; DELETE FROM users")
    assert assert_read_only("SELECT city FROM users LIMIT 10").lower().startswith("select")


def test_role_cannot_run_etl_and_path_stays_in_jail():
    with pytest.raises(PermissionError):
        assert_can("viewer", "etl")
    with pytest.raises(ValueError):
        jail_path("data/extract/../../.env")
    with pytest.raises(ValueError, match="allowlisted"):
        assert_url_allowed("http://169.254.169.254/latest/meta-data", prefixes=["https://pokeapi.co/"])


def test_retry_then_fallback_model(monkeypatch):
    monkeypatch.setenv("LLM_RETRIES", "2")

    class Primary:
        def __init__(self):
            self.calls = 0

        def invoke(self, prompt, **kwargs):
            self.calls += 1
            raise TimeoutError("primary down")

    class Secondary:
        def invoke(self, prompt, **kwargs):
            return AIMessage(content="fallback answer")

    primary = Primary()
    monkeypatch.setattr(
        "utils.llm_pick.fallback_llm",
        lambda: Metered(Secondary(), model="gpt-4o-mini", fallback=True),
    )
    begin()
    result = Metered(primary, model="gpt-4o").invoke("count users")
    assert result.content == "fallback answer"
    assert primary.calls == 2


def test_circuit_opens_after_repeated_failures(monkeypatch):
    monkeypatch.setenv("CIRCUIT_FAILURES", "2")
    breaker = Breaker("llm-test")

    def down():
        raise TimeoutError("down")

    with pytest.raises(TimeoutError):
        call(breaker, down, attempts=1)
    with pytest.raises(TimeoutError):
        call(breaker, down, attempts=1)
    with pytest.raises(CircuitOpen):
        call(breaker, down, attempts=1)


def test_budget_errors_are_not_retried():
    calls = {"n": 0}

    def boom():
        calls["n"] += 1
        raise BudgetExceeded("token budget exceeded")

    with pytest.raises(BudgetExceeded):
        call(Breaker("budget"), boom, attempts=3)
    assert calls["n"] == 1


def test_database_retries_a_dropped_connection(monkeypatch):
    monkeypatch.setenv("DB_RETRIES", "2")
    state = {"attempts": 0}

    class Cursor:
        description = [("city",)]

        def execute(self, query, params=None):
            return None

        def fetchone(self):
            return ("acme",)

        def fetchall(self):
            return [("Halifax",)]

        def close(self):
            return None

    class Conn:
        def __init__(self):
            self.closed = 0

        def cursor(self):
            state["attempts"] += 1
            if state["attempts"] == 1:
                raise psycopg2.OperationalError("connection reset")
            return Cursor()

        def commit(self):
            return None

        def close(self):
            self.closed = 1

    monkeypatch.setattr("utils.database.psycopg2.connect", lambda **kwargs: Conn())
    db = DatabaseUtil.__new__(DatabaseUtil)
    db.db_config = {"host": "fixture"}
    db.connection = Conn()
    result = db.execute_sql("SELECT city FROM users LIMIT 10", tenant_id="acme", role="analyst")
    assert "Halifax" in result
    assert state["attempts"] == 2


def test_audit_log_is_append_only(tmp_path, monkeypatch):
    path = tmp_path / "audit.jsonl"
    monkeypatch.setenv("AUDIT_LOG_PATH", str(path))
    record(request_id="a", user_id="u", tenant_id="acme", question="one", sql="SELECT 1", tools=[], status="ok")
    first = path.read_text(encoding="utf-8")
    record(
        request_id="b",
        user_id="u",
        tenant_id="acme",
        question="two",
        sql="",
        tools=["extract_load_tool"],
        status="ok",
    )
    lines = path.read_text(encoding="utf-8").splitlines()
    assert lines[0] == first.strip()
    assert json.loads(lines[1])["tools"] == ["extract_load_tool"]


def test_metrics_expose_latency_cost_and_errors():
    body = client.get("/metrics")
    assert body.status_code == 200
    text = body.text
    assert "querymesh_http_requests_total" in text
    assert 'quantile="0.5"' in text
    assert 'quantile="0.95"' in text
    assert "querymesh_error_rate" in text
    assert "querymesh_llm_tokens_total" in text
    assert "querymesh_sql_duration_ms_sum" in text
    assert "querymesh_success_rate" in text


def test_one_request_traces_http_to_sql(monkeypatch, tmp_path):
    monkeypatch.setenv("AUDIT_LOG_PATH", str(tmp_path / "audit.jsonl"))
    monkeypatch.setenv("MAX_TOKENS", "50000")
    monkeypatch.setenv("MAX_AGENT_STEPS", "8")
    jobs, limiter = get_backends()
    configure(JobQueue(MemoryRedis()), limiter)

    replies = [
        RouterSchema(answer="sql", comments="analytics"),
        "How many users live in each city",
        "SELECT city FROM users LIMIT 10",
        JudgeSchema(answer="Yes", comments="read"),
        "Halifax is the only city.",
    ]

    class Scripted:
        def invoke(self, prompt, **kwargs):
            reply = replies.pop(0)
            if isinstance(reply, str):
                return AIMessage(content=reply)
            return reply

        def with_structured_output(self, schema):
            return self

        def bind_tools(self, tools):
            return self

    scripted = Scripted()

    def fake_pick(level):
        return Metered(scripted, model="scripted")

    class Cursor:
        description = [("city",)]

        def execute(self, query, params=None):
            return None

        def fetchone(self):
            return ("acme",)

        def fetchall(self):
            return [("Halifax",)]

        def close(self):
            return None

    class Conn:
        closed = 0

        def cursor(self):
            return Cursor()

        def commit(self):
            return None

        def close(self):
            return None

    class FakeDB(DatabaseUtil):
        def __init__(self, cfg):
            self.connection = Conn()
            self.db_config = cfg

        def catalog(self, schema_name, role=""):
            return [{"name": "users", "text": "Table users. Columns: city text.", "refs": []}]

    import agents.data_agent as data_agent_module

    data_agent_module._router = None
    monkeypatch.setattr("agents.data_agent.pick_llm", fake_pick)
    monkeypatch.setattr("agents.sql_analyst.pick_llm", fake_pick)
    monkeypatch.setattr("agents.sql_analyst.analytics_config", lambda: {"host": "fixture"})
    monkeypatch.setattr("agents.sql_analyst.DatabaseUtil", FakeDB)
    before = len(recent)
    try:
        response = client.post(
            "/api/v1/agent/query",
            json={"question": "How many users live in each city"},
            headers={"X-Request-Id": "phase5-trace", **bearer(user_id="trace-user")},
        )
    finally:
        data_agent_module._router = None
        configure(jobs, limiter)
    assert response.status_code == 200, response.text
    assert response.json()["answer"] == "Halifax is the only city."
    assert "sql" not in response.json()
    spans = [item for item in recent[before:] if item.get("request_id") == "phase5-trace"]
    names = [item["name"] for item in spans]
    for required in ("http", "agent", "router", "sql", "db.execute", "llm"):
        assert required in names
    trace_ids = {item.get("trace_id") for item in spans if item.get("trace_id")}
    if trace_ids:
        assert len(trace_ids) == 1
    row = json.loads((tmp_path / "audit.jsonl").read_text(encoding="utf-8").splitlines()[-1])
    assert row["user_id"] == "trace-user"
    assert row["tenant_id"] == "acme"
    assert row["sql"].lower().startswith("select")
    assert row["status"] == "ok"
    metrics_body = client.get("/metrics").text
    assert 'querymesh_agent_routes_total{route="sql"}' in metrics_body


def test_open_circuit_is_a_503(monkeypatch):
    def explode(question, user):
        raise CircuitOpen("llm")

    monkeypatch.setattr(routes, "invoke_agent", explode)
    response = client.post(
        "/api/v1/agent/query",
        json={"question": "count rides today"},
        headers=bearer(user_id="circuit-user"),
    )
    assert response.status_code == 503
    assert "unavailable" in response.json()["detail"]


def test_nested_spans_keep_the_request_id():
    set_request_id("rid-local")
    with span("http"):
        with span("sql"):
            with span("db.execute"):
                pass
    matched = [item for item in recent if item.get("request_id") == "rid-local"]
    assert [item["name"] for item in matched] == ["db.execute", "sql", "http"]


def test_eval_script_reports_a_score():
    from eval.run_eval import judge, main

    bad = judge(
        {
            "expected_route": "sql",
            "route": "sql",
            "tables": ["users"],
            "fact": "city",
            "sql": "DROP TABLE users",
            "answer": "dropped",
        }
    )
    assert bad["sql_ok"] is False
    text = main()
    assert text.startswith("cases=50 ")
    assert "score=1.000" in text
