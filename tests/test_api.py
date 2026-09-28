"""API contract checks that do not call the LLM or a live database."""

import os
import time

import jwt

os.environ["JWT_SECRET"] = "test-secret-at-least-32-bytes-long"

import api.app as app_module
import api.routes as routes
from api.policy import column_visible, mask, route_target
from fastapi.testclient import TestClient
from utils.database import DatabaseUtil

client = TestClient(app_module.app)


def bearer(role="analyst", tenant_id="acme", secret="test-secret-at-least-32-bytes-long", exp_delta=3600):
    payload = {"sub": "user_42", "role": role, "exp": int(time.time()) + exp_delta}
    if tenant_id is not None:
        payload["tenant_id"] = tenant_id
    token = jwt.encode(payload, secret, algorithm="HS256")
    return {"Authorization": f"Bearer {token}"}


def test_health_sets_request_id():
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert response.headers["x-request-id"]


def test_inbound_request_id_is_echoed():
    response = client.get("/health", headers={"X-Request-Id": "trace-123"})
    assert response.headers["x-request-id"] == "trace-123"


def test_ready_fails_when_postgres_is_down(monkeypatch):
    monkeypatch.setattr(app_module, "postgres_reachable", lambda: False)
    response = client.get("/ready")
    assert response.status_code == 503
    assert response.json()["database"] == "down"
    assert response.headers["x-request-id"]


def test_ready_ok_when_postgres_is_up(monkeypatch):
    monkeypatch.setattr(app_module, "postgres_reachable", lambda: True)
    response = client.get("/ready")
    assert response.status_code == 200
    assert response.json() == {"status": "ready", "database": "up"}


def test_query_validation_error():
    response = client.post("/api/v1/agent/query", json={}, headers=bearer())
    assert response.status_code == 422
    assert response.json()["error"] == "validation_error"
    assert response.headers["x-request-id"]


def test_query_returns_answer_and_route(monkeypatch):
    monkeypatch.setattr(
        routes,
        "run_query",
        lambda question, user: {"answer": f"echo:{question}", "route": "sql"},
    )
    response = client.post(
        "/api/v1/agent/query",
        json={"question": "top users"},
        headers=bearer(),
    )
    assert response.status_code == 200
    assert response.json() == {"answer": "echo:top users", "route": "sql"}


def test_query_requires_token():
    response = client.post("/api/v1/agent/query", json={"question": "top users"})
    assert response.status_code == 401


def test_query_rejects_bad_signature():
    response = client.post(
        "/api/v1/agent/query",
        json={"question": "top users"},
        headers=bearer(secret="wrong"),
    )
    assert response.status_code == 401


def test_query_rejects_expired_token():
    response = client.post(
        "/api/v1/agent/query",
        json={"question": "top users"},
        headers=bearer(exp_delta=-10),
    )
    assert response.status_code == 401


def test_query_rejects_token_without_tenant():
    response = client.post(
        "/api/v1/agent/query",
        json={"question": "top users"},
        headers=bearer(tenant_id=None),
    )
    assert response.status_code == 401


def test_health_and_ready_stay_public(monkeypatch):
    monkeypatch.setattr(app_module, "postgres_reachable", lambda: True)
    assert client.get("/health").status_code == 200
    assert client.get("/ready").status_code == 200


def test_viewer_etl_is_forbidden_and_node_is_not_entered(monkeypatch):
    entered = []

    def invoke(question, user):
        node = route_target("etl", user.role)
        entered.append(node)

    monkeypatch.setattr(routes, "invoke_agent", invoke)
    response = client.post(
        "/api/v1/agent/query",
        json={"question": "export the users table"},
        headers=bearer(role="viewer"),
    )
    assert response.status_code == 403
    assert entered == []


def test_cross_tenant_question_does_not_call_the_agent(monkeypatch):
    def invoke(question, user):
        raise AssertionError("agent invoked")

    monkeypatch.setattr(routes, "invoke_agent", invoke)
    response = client.post(
        "/api/v1/agent/query",
        json={"question": "show rows where tenant_id = 'other'"},
        headers=bearer(tenant_id="acme"),
    )
    assert response.status_code == 403


def test_viewer_cannot_see_sensitive_columns():
    assert column_visible("viewer", "users", "phone") is False
    assert column_visible("viewer", "users", "email") is False
    assert column_visible("viewer", "users", "city") is True
    assert mask("analyst", "users", "email", "a@b.c") is None
    assert mask("admin", "users", "phone", "555") == "555"


def test_execute_sql_sets_tenant_and_masks_cells():
    class Cursor:
        def __init__(self):
            self.calls = []
            self.description = [("email",), ("city",)]

        def execute(self, query, params=None):
            self.calls.append((query, params))

        def fetchone(self):
            return ("acme",)

        def fetchall(self):
            return [("a@b.c", "Halifax")]

        def close(self):
            pass

    class Conn:
        def __init__(self):
            self.cursor_obj = Cursor()

        def cursor(self):
            return self.cursor_obj

        def commit(self):
            pass

        def close(self):
            pass

    db = DatabaseUtil.__new__(DatabaseUtil)
    db.connection = Conn()
    result = db.execute_sql("SELECT email, city FROM users", tenant_id="acme", role="analyst")
    assert db.connection.cursor_obj.calls[0][0].startswith("SELECT set_config")
    assert db.connection.cursor_obj.calls[0][1] == ("acme",)
    assert "a@b.c" not in result
    assert "Halifax" in result


def test_execute_sql_refuses_missing_tenant():
    db = DatabaseUtil.__new__(DatabaseUtil)
    try:
        db.execute_sql("SELECT 1")
    except ValueError as exc:
        assert "tenant_id" in str(exc)
    else:
        raise AssertionError("query ran without a tenant")


def test_schema_details_hides_viewer_columns():
    class Cursor:
        def __init__(self):
            self.queries = []
            self._rows = [
                [("users",)],
                [("email", "text"), ("phone", "text"), ("city", "text")],
                [("Halifax",)],
            ]

        def execute(self, query, params=None):
            self.queries.append(query)

        def fetchall(self):
            return self._rows.pop(0)

        def close(self):
            pass

    class Conn:
        encoding = "UTF8"

        def __init__(self):
            self.cursor_obj = Cursor()

        def cursor(self):
            return self.cursor_obj

        def close(self):
            pass

    db = DatabaseUtil.__new__(DatabaseUtil)
    db.connection = Conn()
    text = db.schema_details("public", role="viewer")
    assert "phone" not in text
    assert "email" not in text
    assert "city" in text
    sample_sql = repr(db.connection.cursor_obj.queries[-1])
    assert "phone" not in sample_sql
    assert "email" not in sample_sql
    assert "Identifier('city')" in sample_sql


def test_openapi_lists_v1_query():
    response = client.get("/openapi.json")
    assert response.status_code == 200
    spec = response.json()
    assert "/api/v1/agent/query" in spec["paths"]
    assert "/health" in spec["paths"]
    assert "/ready" in spec["paths"]


def test_answer_from_sql_and_etl_results():
    sql = {
        "route_response": "sql",
        "messages": [{"content": "q"}, {"final_answer": "Three payment methods."}],
    }
    assert routes.answer_from_result(sql) == ("Three payment methods.", "sql")

    etl = {
        "route_response": "etl",
        "messages": [
            {
                "messages": [
                    {"content": "calling tool", "tool_calls": [{"id": "1"}]},
                    {"content": "Saved CSV under data/extract."},
                ]
            }
        ],
    }
    assert routes.answer_from_result(etl) == ("Saved CSV under data/extract.", "etl")
