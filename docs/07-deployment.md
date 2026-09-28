# 07 — Deployment

**Goal:** One command runs a prod-like stack, and main cannot merge without tests.

Depends on [02-api.md](./02-api.md) and [06-observability-testing.md](./06-observability-testing.md).

## Build

1. Dockerfile for the API. Separate image or command for the ETL worker once [04-safe-execution.md](./04-safe-execution.md) exists.
2. `docker-compose.yml`: API, Postgres (with the read-only role), Redis. Add the worker and broker when jobs land.
3. CI: install, lint, unit tests, image build. Do not run live LLM calls in CI.
4. Production secrets from a secret manager. Images must not contain `.env`.
5. Postgres backup and a documented restore. `feed_db.py` stays a dev seed, not the production migration path.
6. `POST /api/v1/feedback` stores thumbs and comment against `request_id` for later eval.

Move `faker` and `ipython` out of runtime dependencies in `pyproject.toml`. They are demo tools.

## Done when

- [ ] `docker compose up` serves `/ready`
- [ ] CI fails on a broken SQL-gate test
- [ ] No passwords or API keys in the image or git history
- [ ] Restore steps have been run once against a copy
