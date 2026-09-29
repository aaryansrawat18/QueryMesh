# 07 — Deployment

**Goal:** One command runs a prod-like stack, and main cannot merge without tests.

Depends on [02-api.md](./02-api.md) and [06-observability-testing.md](./06-observability-testing.md).

## Build

1. `Dockerfile` runs the API. The same image runs the worker with `python -m workers.etl_worker`.
2. `docker-compose.yml`: API, worker, Postgres (read-only role `querymesh_ro`), Redis. Redis is the broker from Phase 3.
3. CI (`.github/workflows/test.yml`): ruff, unit tests, offline eval, image build. Image push to GHCR on `main`. No live LLM calls.
4. `QUERYMESH_ENV=production` refuses a `.env` inside the image and refuses placeholder secrets. Cloud Run reads Secret Manager (`deploy/cloudrun.yaml`). Local compose defaults stay out of the image.
5. `python -m utils.pg_backup` writes `backups/<database>.sql` and restores the marker row into `<database>_restore`. `feed_db.py` stays a dev seed. For a full schema dump, `pg_dump` from the Postgres image.
6. `POST /api/v1/feedback` stores thumbs and a comment against an audited `request_id`. `eval/run_eval.py` counts down votes and prints prompt notes.

`faker` and `ipython` are the `demo` extra, not runtime dependencies.

## Run

```bash
docker compose up --build
```

Probes: `GET /health`, `GET /ready` on port 8000.

Restore drill (second database, needs the compose Postgres up):

```bash
docker compose --profile drill run --rm backup
```

Cloud Run (only when `GCP_PROJECT`, `GCP_REGION`, and `GCP_SA_KEY` are set on the repo): the `deploy` job ships the GHCR image and wires Secret Manager. Create `querymesh-jwt`, `querymesh-analytics-password`, `querymesh-analytics-host`, and `querymesh-redis-url` before the first deploy. The service YAML probes `/health`.

## Done when

- [x] `docker compose up` serves `/ready`
- [x] CI fails on a broken SQL-gate test (`pytest` runs `tests/test_phase5.py`)
- [x] No passwords or API keys in the image
- [x] Restore steps have been run once against a copy (`querymesh_restore.backup_drill` returned `phase6-restore-drill`)
