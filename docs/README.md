# QueryMesh production docs

Work packets to take this tutorial from CLI demo to a deployable service. Do them in order. Phase 0 is a blocker: SQL and ETL can still mutate data or run arbitrary Python.

Existing overview: [`../PRODUCTION_ROADMAP.md`](../PRODUCTION_ROADMAP.md). These files are the implementation checklist.

| Order | File | Outcome |
|-------|------|---------|
| 0 | [01-security.md](./01-security.md) | No secrets in source, read-only SQL, no unrestricted `exec` |
| 1 | [02-api.md](./02-api.md) | FastAPI service with health, request ids, OpenAPI |
| 2 | [03-auth-tenancy.md](./03-auth-tenancy.md) | Authenticated, role-scoped, tenant-isolated queries |
| 3 | [04-safe-execution.md](./04-safe-execution.md) | Sandboxed ETL, job queue, rate limits |
| 4 | [05-agent-quality.md](./05-agent-quality.md) | Schema RAG, guardrails, cost caps |
| 5 | [06-observability-testing.md](./06-observability-testing.md) | Traces, tests, eval harness |
| 6 | [07-deployment.md](./07-deployment.md) | Docker, CI, secrets, backups |

## Status

Packets 0–6 are in this tree. Exit checks are the boxes in [`PRODUCTION_ROADMAP.md`](../PRODUCTION_ROADMAP.md). Apply `sql/tenant_rls.sql` on Postgres for database-side tenant isolation. `docker compose up` is the local prod-like stack.

## Done when

A stranger can run `docker compose up`, call `POST /api/v1/agent/query` with a token, get a traced answer, and a bad SQL or Python payload fails closed.
