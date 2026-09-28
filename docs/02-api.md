# 02 — HTTP API

**Goal:** Replace the CLI-only entry in `main.py` with a versioned service.

## Build

1. FastAPI app, run with uvicorn.
2. `POST /api/v1/agent/query` — body `{ "question": "..." }`, calls `data_agent.invoke`, returns the final answer plus route (`sql` or `etl`).
3. Pydantic request, response, and error models. OpenAPI at `/docs`.
4. `GET /health` (process up) and `GET /ready` (Postgres reachable).
5. Middleware: generate `request_id`, return it as `X-Request-Id`, log route, status, and latency as structured lines. No `print` for request handling.

Keep `main.py` as a thin local runner that calls the same function the route uses, so CLI and API cannot drift.

## Touch

- New `api/` package (`app.py`, `routes.py`, `schemas.py`)
- `pyproject.toml` — add `fastapi` and `uvicorn`
- `main.py` — stop hardcoding the PokeAPI prompt as the only entry

## Done when

- [x] `uvicorn` serves the agent
- [x] `/docs` shows the v1 contract
- [x] `/ready` fails when Postgres is down
- [x] Every response carries `X-Request-Id`
