# 04 — Safe ETL and background jobs

**Goal:** Generated Python never runs in the API process, and long extracts do not hold an HTTP worker.

Depends on [01-security.md](./01-security.md) and [02-api.md](./02-api.md).

## Build

1. **Sandbox.** Run ETL Python in a separate worker or container: CPU, memory, and wall-clock limits; no host filesystem except a per-job output dir; no network unless the extract step explicitly needs it.
2. **Egress allowlist.** `extract_load` may call only approved URL prefixes. Reject link-local, metadata, and private addresses (SSRF).
3. **Path jail.** Output paths must stay under `data/extract` or `data/transform`. Reject `..` and absolute paths outside that root.
4. **Queue.** `POST /api/v1/jobs` enqueues ETL and returns a job id. Worker consumes the queue (Celery + Redis, or one equivalent). `GET /api/v1/jobs/{id}` returns status.
5. **Rate limits.** Per user and tenant, backed by Redis, before the LLM router runs.
6. **Read replica.** Agent SQL uses the analytics / read-only database from phase 0, not a primary app database.

## Done when

- [x] API process has no `exec` of model code
- [x] ETL returns a job id immediately
- [x] A non-allowlisted URL is rejected
- [x] Excess requests return 429
