"""Phase 3: sandbox, egress, queue, rate limit, analytics database."""

import os
import time
from pathlib import Path

import jwt
import pytest

os.environ["JWT_SECRET"] = "test-secret-at-least-32-bytes-long"
os.environ["RATE_LIMIT_USER"] = "100000"
os.environ["RATE_LIMIT_TENANT"] = "100000"

import api.app as app_module
import api.routes as routes
from fastapi.testclient import TestClient
from utils.database import analytics_config
from utils.etl_tools import ETLTools
from utils.guard import assert_url_allowed, jail_path
from utils.jobs import JobQueue, process_next
from utils.ratelimit import RateLimiter
from utils.redis_client import MemoryRedis
from utils.runtime import configure, get_backends
from utils.sandbox import run_isolated

client = TestClient(app_module.app)
ROOT = Path(__file__).resolve().parents[1]


def _stub_anthropic():
    """This environment has LangGraph but not the Anthropic SDK. Stub the client."""
    import sys
    import types

    if "langchain_anthropic" in sys.modules:
        return
    module = types.ModuleType("langchain_anthropic")

    class ChatAnthropic:
        def __init__(self, *args, **kwargs):
            self.kwargs = kwargs

        def with_structured_output(self, schema):
            return self

    module.ChatAnthropic = ChatAnthropic
    sys.modules["langchain_anthropic"] = module


def bearer(role="analyst", tenant_id="acme", user_id="user_42"):
    payload = {
        "sub": user_id,
        "role": role,
        "tenant_id": tenant_id,
        "exp": int(time.time()) + 3600,
    }
    token = jwt.encode(payload, os.environ["JWT_SECRET"], algorithm="HS256")
    return {"Authorization": f"Bearer {token}"}


def test_api_does_not_exec_model_code():
    paths = list((ROOT / "api").glob("*.py"))
    paths += [
        ROOT / "utils" / "etl_tools.py",
        ROOT / "agents" / "etl_analyst.py",
        ROOT / "agents" / "data_agent.py",
    ]
    offenders = [path.name for path in paths if "exec(" in path.read_text(encoding="utf-8")]
    assert offenders == []


def test_etl_tools_refuse_the_api_process(monkeypatch):
    monkeypatch.delenv("QUERYMESH_WORKER", raising=False)
    with pytest.raises(RuntimeError, match="worker"):
        ETLTools().execute_code("print(1)", ROOT / "data" / "transform")


def test_url_allowlist_and_private_addresses():
    def explode(_host):
        raise AssertionError("dns should not run")

    with pytest.raises(ValueError, match="allowlisted"):
        assert_url_allowed("https://evil.example/x", prefixes=["https://pokeapi.co/"], resolve=explode)

    with pytest.raises(ValueError, match="blocked"):
        assert_url_allowed(
            "http://127.0.0.1/latest/meta-data",
            prefixes=["http://127.0.0.1/"],
            resolve=lambda _host: ["127.0.0.1"],
        )

    with pytest.raises(ValueError, match="blocked"):
        assert_url_allowed(
            "https://pokeapi.co/api/v2/pokemon",
            prefixes=["https://pokeapi.co/"],
            resolve=lambda _host: ["169.254.169.254"],
        )

    assert (
        assert_url_allowed(
            "https://pokeapi.co/api/v2/pokemon",
            prefixes=["https://pokeapi.co/"],
            resolve=lambda _host: ["1.1.1.1"],
        )
        == "https://pokeapi.co/api/v2/pokemon"
    )


def test_output_paths_stay_in_the_data_dirs():
    assert jail_path("data/extract").name == "extract"
    assert jail_path("data/transform/out.csv").name == "out.csv"
    with pytest.raises(ValueError):
        jail_path("data/extract/../../secrets.txt")
    with pytest.raises(ValueError):
        jail_path("C:/Windows/Temp/out.csv")
    with pytest.raises(ValueError):
        jail_path("data/other/file.csv")


def test_sandbox_is_a_child_process_with_no_secrets(tmp_path, monkeypatch):
    monkeypatch.setenv("JWT_SECRET", "super-secret-value")
    monkeypatch.setenv("password", "db-secret-value")
    result = run_isolated(
        "import os\nprint(os.environ.get('JWT_SECRET', ''))\nprint('sandbox-ok')\n",
        tmp_path,
        timeout=20,
    )
    assert "sandbox-ok" in result
    assert "super-secret-value" not in result
    assert "db-secret-value" not in result

    blocked = run_isolated("import socket\n", tmp_path, timeout=20)
    assert "blocked import" in blocked

    escaped = run_isolated("open(r'C:/Windows/win.ini', 'r')\n", tmp_path, timeout=20)
    assert "outside the job directory" in escaped


def test_post_job_returns_immediately_and_stays_queued(monkeypatch):
    def runner(_job):
        raise AssertionError("worker ran inside the request")

    monkeypatch.setattr("workers.etl_worker.run_etl", runner)
    response = client.post(
        "/api/v1/jobs",
        json={"question": "extract the pokemon endpoint"},
        headers=bearer(),
    )
    assert response.status_code == 202
    body = response.json()
    assert body["status"] == "queued"
    follow = client.get(f"/api/v1/jobs/{body['job_id']}", headers=bearer())
    assert follow.status_code == 200
    assert follow.json()["status"] == "queued"


def test_job_is_hidden_from_other_tenants():
    created = client.post("/api/v1/jobs", json={"question": "export rides"}, headers=bearer())
    job_id = created.json()["job_id"]
    other = client.get(f"/api/v1/jobs/{job_id}", headers=bearer(tenant_id="other"))
    assert other.status_code == 404


def test_cross_tenant_job_is_not_enqueued(monkeypatch):
    def boom(**_kwargs):
        raise AssertionError("enqueued")

    monkeypatch.setattr("api.jobs.enqueue_etl", boom)
    response = client.post(
        "/api/v1/jobs",
        json={"question": "rows where tenant_id = 'other'"},
        headers=bearer(),
    )
    assert response.status_code == 403


def test_etl_node_queues_instead_of_running_tools(monkeypatch):
    _stub_anthropic()
    import agents.data_agent as agent

    monkeypatch.setattr(agent, "enqueue_etl", lambda **_kwargs: "job-123")

    class Message:
        content = "extract pokemon"

    class State:
        role = "analyst"
        tenant_id = "acme"
        messages = [Message()]

    result = agent.etl_node(State())
    assert "job-123" in result.messages[-1].content


def test_viewer_cannot_enqueue_etl():
    response = client.post(
        "/api/v1/jobs",
        json={"question": "export rides"},
        headers=bearer(role="viewer"),
    )
    assert response.status_code == 403


def test_worker_marks_a_finished_job():
    store = JobQueue(MemoryRedis())
    job_id = store.enqueue(question="extract", user_id="u", tenant_id="acme", role="analyst")
    assert process_next(store, lambda _job: "saved", timeout=0) == job_id
    assert store.get(job_id)["status"] == "succeeded"
    assert store.get(job_id)["result"] == "saved"


def test_excess_requests_return_429(monkeypatch):
    calls = []
    monkeypatch.setattr(routes, "run_query", lambda question, user: calls.append(question) or {"answer": "ok", "route": "sql"})
    jobs, previous = get_backends()
    configure(jobs, RateLimiter(MemoryRedis(), user_limit=1, tenant_limit=100, window=60))
    try:
        headers = bearer(user_id="rate-user")
        first = client.post("/api/v1/agent/query", json={"question": "count users"}, headers=headers)
        second = client.post("/api/v1/agent/query", json={"question": "count users"}, headers=headers)
    finally:
        configure(jobs, previous)
    assert first.status_code == 200
    assert second.status_code == 429
    assert calls == ["count users"]


def test_tenant_bucket_is_shared(monkeypatch):
    monkeypatch.setattr(routes, "run_query", lambda question, user: {"answer": "ok", "route": "sql"})
    jobs, previous = get_backends()
    configure(jobs, RateLimiter(MemoryRedis(), user_limit=100, tenant_limit=1, window=60))
    try:
        first = client.post(
            "/api/v1/agent/query",
            json={"question": "count users"},
            headers=bearer(user_id="tenant-a"),
        )
        second = client.post(
            "/api/v1/agent/query",
            json={"question": "count users"},
            headers=bearer(user_id="tenant-b"),
        )
    finally:
        configure(jobs, previous)
    assert first.status_code == 200
    assert second.status_code == 429


def test_agent_reads_the_analytics_database(monkeypatch):
    monkeypatch.setattr("utils.database.load_dotenv", lambda *args, **kwargs: None)
    monkeypatch.setenv("host", "primary.internal")
    monkeypatch.setenv("database", "primary_db")
    monkeypatch.setenv("ANALYTICS_HOST", "analytics.internal")
    monkeypatch.setenv("ANALYTICS_PORT", "5432")
    monkeypatch.setenv("ANALYTICS_USER", "querymesh_ro")
    monkeypatch.setenv("ANALYTICS_PASSWORD", "")
    monkeypatch.setenv("ANALYTICS_DATABASE", "analytics")
    assert analytics_config()["host"] == "analytics.internal"
    assert analytics_config()["dbname"] == "analytics"

    monkeypatch.delenv("ANALYTICS_HOST", raising=False)
    with pytest.raises(RuntimeError):
        analytics_config()

    _stub_anthropic()
    import agents.sql_analyst as sql

    seen = {}

    def fake_config():
        seen["host"] = "analytics.internal"
        return {"host": "analytics.internal", "port": 5432, "user": "ro", "password": "", "dbname": "analytics"}

    class DB:
        def __init__(self, cfg):
            seen["cfg"] = cfg

        def schema_details(self, schema, role=""):
            return "columns"

        def execute_sql(self, query, tenant_id="", role=""):
            seen["tenant"] = tenant_id
            return "[]"

    monkeypatch.setattr(sql, "analytics_config", fake_config)
    monkeypatch.setattr(sql, "DatabaseUtil", DB)
    state = sql.AgentSchema(
        messages=[],
        user_question="q",
        curated_ques="curated",
        prompt_query_context="",
        generated_sql_query="SELECT 1",
        is_safe="Yes",
        comments="",
        sql_query_execution_result="",
        final_answer="",
        tenant_id="acme",
        role="analyst",
    )
    sql.prompt_query_context(state)
    sql.execute_sql(state)
    assert seen["cfg"]["host"] == "analytics.internal"
    assert seen["tenant"] == "acme"
    source = (ROOT / "agents" / "sql_analyst.py").read_text(encoding="utf-8")
    assert "os.environ['host']" not in source
    assert "os.environ[\"host\"]" not in source
