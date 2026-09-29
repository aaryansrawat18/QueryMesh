"""Phase 6: feedback, production secrets, image recipe, backup SQL."""

import json
import os
import time
from pathlib import Path

import jwt
import pytest

os.environ.setdefault("JWT_SECRET", "test-secret-at-least-32-bytes-long")
os.environ["RATE_LIMIT_USER"] = "100000"
os.environ["RATE_LIMIT_TENANT"] = "100000"

import api.app as app_module
from fastapi.testclient import TestClient
from utils.feedback import prompt_notes
from utils.pg_backup import apply_script, marker_script, render_dump
from utils.secrets import prepare_runtime

client = TestClient(app_module.app)
ROOT = Path(__file__).resolve().parents[1]


def bearer(role="analyst", tenant_id="acme"):
    payload = {
        "sub": "user_42",
        "role": role,
        "tenant_id": tenant_id,
        "exp": int(time.time()) + 3600,
    }
    token = jwt.encode(payload, os.environ["JWT_SECRET"], algorithm="HS256")
    return {"Authorization": f"Bearer {token}"}


def _audit(tmp_path, request_id, tenant_id, question):
    path = tmp_path / "audit.jsonl"
    path.write_text(
        json.dumps(
            {
                "request_id": request_id,
                "tenant_id": tenant_id,
                "user_id": "user_42",
                "question": question,
                "status": "ok",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    return path


def test_feedback_requires_a_token():
    response = client.post(
        "/api/v1/feedback",
        json={"request_id": "trace-1", "rating": "up"},
    )
    assert response.status_code == 401


def test_feedback_rejects_unknown_and_other_tenant(tmp_path, monkeypatch):
    audit = _audit(tmp_path, "trace-1", "beta", "secret question")
    monkeypatch.setenv("AUDIT_LOG_PATH", str(audit))
    monkeypatch.setenv("FEEDBACK_LOG_PATH", str(tmp_path / "feedback.jsonl"))
    response = client.post(
        "/api/v1/feedback",
        json={"request_id": "trace-1", "rating": "down", "comment": "nope"},
        headers=bearer(tenant_id="acme"),
    )
    assert response.status_code == 404
    assert not (tmp_path / "feedback.jsonl").exists()


def test_feedback_stores_the_audited_question(tmp_path, monkeypatch):
    audit = _audit(tmp_path, "trace-1", "acme", "How many vehicles")
    feedback = tmp_path / "feedback.jsonl"
    monkeypatch.setenv("AUDIT_LOG_PATH", str(audit))
    monkeypatch.setenv("FEEDBACK_LOG_PATH", str(feedback))
    response = client.post(
        "/api/v1/feedback",
        json={"request_id": "trace-1", "rating": "down", "comment": "wrong table"},
        headers=bearer(),
    )
    assert response.status_code == 201
    assert response.json()["stored"] is True
    row = json.loads(feedback.read_text(encoding="utf-8").strip())
    assert row["question"] == "How many vehicles"
    assert row["rating"] == "down"
    assert row["tenant_id"] == "acme"
    spec = client.get("/openapi.json").json()
    assert "/api/v1/feedback" in spec["paths"]


def test_eval_reports_downvotes_for_prompt_iteration():
    from eval.run_eval import summarize

    text = summarize(
        [
            {
                "question": "known",
                "route": "sql",
                "expected_route": "sql",
                "sql": "SELECT 1",
                "answer": "one",
                "fact": "",
            }
        ],
        [
            {"rating": "down", "question": "brand new failure", "comment": "missed payments"},
            {"rating": "up", "question": "known", "comment": "nice"},
        ],
    )
    assert "feedback_down=1" in text
    assert "uncovered=1" in text
    assert text.endswith("score=1.000")
    notes = prompt_notes(
        [
            {"rating": "down", "question": "q", "comment": "fix the join"},
            {"rating": "up", "question": "q", "comment": "nice"},
        ]
    )
    assert "fix the join" in notes
    assert "nice" not in notes


def test_dump_escapes_quotes_and_keeps_the_marker():
    script = render_dump(
        [
            {"name": "users", "columns": ["id"], "rows": [(1,)]},
            {"name": "backup_drill", "columns": ["id", "note"], "rows": [(1, "o'brien;x")]},
        ]
    )
    assert "o''brien;x" in script
    kept = marker_script(script)
    assert 'INSERT INTO "backup_drill"' in kept
    assert "users" not in kept

    class Cursor:
        def __init__(self):
            self.statements = []

        def execute(self, statement):
            self.statements.append(statement)

    cursor = Cursor()
    apply_script(cursor, kept)
    assert cursor.statements == [
        'DELETE FROM "backup_drill"',
        'INSERT INTO "backup_drill" ("id", "note") VALUES (1, \'o\'\'brien;x\')',
    ]


def test_production_refuses_a_baked_env_and_placeholder_secrets(tmp_path, monkeypatch):
    for name in ("JWT_SECRET", "ANALYTICS_HOST", "ANALYTICS_USER", "ANALYTICS_PASSWORD", "ANALYTICS_DATABASE"):
        if name in os.environ:
            monkeypatch.setenv(name, os.environ[name])
        else:
            monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("QUERYMESH_ENV", "production")
    monkeypatch.setenv("QUERYMESH_IMAGE_ROOT", str(tmp_path))
    monkeypatch.delenv("QUERYMESH_SECRETS_DIR", raising=False)
    (tmp_path / ".env").write_text("JWT_SECRET=from-image\n", encoding="utf-8")
    with pytest.raises(RuntimeError, match="baked"):
        prepare_runtime()

    (tmp_path / ".env").unlink()
    for name in ("JWT_SECRET", "ANALYTICS_HOST", "ANALYTICS_USER", "ANALYTICS_PASSWORD", "ANALYTICS_DATABASE"):
        monkeypatch.delenv(name, raising=False)
    secret_dir = tmp_path / "secrets"
    secret_dir.mkdir()
    values = {
        "JWT_SECRET": "x" * 40,
        "ANALYTICS_HOST": "db.internal",
        "ANALYTICS_USER": "querymesh_ro",
        "ANALYTICS_PASSWORD": "querymesh_ro_local",
        "ANALYTICS_DATABASE": "querymesh",
    }
    for name, value in values.items():
        (secret_dir / name).write_text(value, encoding="utf-8")
    monkeypatch.setenv("QUERYMESH_SECRETS_DIR", str(secret_dir))
    with pytest.raises(RuntimeError, match="ANALYTICS_PASSWORD"):
        prepare_runtime()

    (secret_dir / "ANALYTICS_PASSWORD").write_text("a-real-db-password", encoding="utf-8")
    prepare_runtime()
    assert os.environ["JWT_SECRET"] == "x" * 40
    assert os.environ["ANALYTICS_PASSWORD"] == "a-real-db-password"


def test_image_and_workflow_keep_secrets_out():
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    ignore = (ROOT / ".dockerignore").read_text(encoding="utf-8")
    workflow = (ROOT / ".github" / "workflows" / "test.yml").read_text(encoding="utf-8")
    compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    init = (ROOT / "deploy" / "postgres" / "init-readonly.sh").read_text(encoding="utf-8")
    assert ".env" in ignore.splitlines()
    for line in dockerfile.splitlines():
        stripped = line.strip()
        if stripped.startswith(("COPY", "ADD", "ENV")):
            assert ".env" not in stripped
            assert "PASSWORD" not in stripped
            assert "API_KEY" not in stripped
    assert "ruff check" in workflow
    assert "pytest -q" in workflow
    assert "docker/build-push-action" in workflow
    assert "python eval/run_eval.py" in workflow
    for service in ("api:", "worker:", "postgres:", "redis:"):
        assert service in compose
    assert "querymesh_ro" in init
    assert "default_transaction_read_only" in init
