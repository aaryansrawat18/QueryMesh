# 03 — Auth, roles, and tenancy

**Goal:** Only a known user can call the agent, and that user can only see their own tenant’s data.

Depends on [02-api.md](./02-api.md). `/health` and `/ready` stay public. Everything under `/api/v1` requires a token.

## What you are building

One request does four cheap checks, in this order. Stop at the first failure. Do not call the LLM until all four pass.

```text
Authorization: Bearer <token>
        |
        v
1. Token valid?          no -> 401
        |
        v
2. Role allowed?         no -> 403   (Viewer cannot run ETL)
        |
        v
3. tenant_id present?    no -> 401
        |
        v
4. Postgres RLS          bad SQL still returns zero rows from other tenants
        |
        v
   data_agent.invoke(...)
```

Identity lives **in the token**, not in a users table.

```json
{
  "sub": "user_42",
  "role": "analyst",
  "tenant_id": "acme",
  "exp": 1760000000
}
```

## What to skip

These look like “real auth” and are not needed for this phase:

| Skip | Use instead |
|------|-------------|
| OAuth server, login pages, refresh tokens | Sign a JWT yourself. The API only **verifies** it. |
| Users table + password hashes | Claims above. Add a table later if you need revoke-before-expiry. |
| Casbin / OPA / policy engine | One dict: role → what it may do. |
| Rewriting the LLM’s SQL to inject `tenant_id` | `SET LOCAL` + Postgres row-level security. The database enforces it even when the SQL is wrong. |
| Auth middleware class | A FastAPI `Depends`. Easier to test, and `/health` stays open without a path allowlist. |

One new dependency: `PyJWT`. Pin the algorithm to `HS256` so a token cannot switch to `none`.

## 2.1 Verify the JWT

New file `api/auth.py`. Secret comes from the environment (`JWT_SECRET`). Reject missing, expired, or wrongly signed tokens.

```python
import os
from dataclasses import dataclass

import jwt
from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

_bearer = HTTPBearer(auto_error=False)
ROLES = ("admin", "analyst", "viewer")


@dataclass(frozen=True)
class CurrentUser:
    user_id: str
    role: str
    tenant_id: str


def current_user(
    creds: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> CurrentUser:
    if creds is None or creds.scheme.lower() != "bearer":
        raise HTTPException(status_code=401, detail="missing bearer token")
    try:
        payload = jwt.decode(
            creds.credentials,
            os.environ["JWT_SECRET"],
            algorithms=["HS256"],
        )
    except jwt.PyJWTError:
        raise HTTPException(status_code=401, detail="invalid token")

    role = payload.get("role")
    tenant_id = payload.get("tenant_id")
    user_id = payload.get("sub")
    if role not in ROLES or not tenant_id or not user_id:
        raise HTTPException(status_code=401, detail="token missing required claims")
    return CurrentUser(user_id=user_id, role=role, tenant_id=str(tenant_id))
```

Wire it only on the agent route in `api/routes.py`. Do **not** put it on `/health` or `/ready`.

```python
def query_agent(
    body: QueryRequest,
    user: CurrentUser = Depends(current_user),
) -> dict:
    return run_query(body.question, user)
```

Issue tokens with a one-off script (not an endpoint). Short expiry, for example 1 hour. Rotate `JWT_SECRET` by refusing the old secret after you re-issue tokens.

```python
import jwt, os, time
print(jwt.encode(
    {"sub": "user_42", "role": "analyst", "tenant_id": "acme", "exp": int(time.time()) + 3600},
    os.environ["JWT_SECRET"],
    algorithm="HS256",
))
```

Add `JWT_SECRET=` to `.env.example`. Never commit a real value.

## 2.2 Roles — one dict, checked before the agent

Three roles. Permissions are actions, not table names. Table and column rules are section 2.5.

| Role | SQL questions | ETL |
|------|---------------|-----|
| `admin` | yes | yes |
| `analyst` | yes | yes |
| `viewer` | yes | no |

```python
# api/policy.py
CAN = {
    "admin": {"sql", "etl"},
    "analyst": {"sql", "etl"},
    "viewer": {"sql"},
}


def assert_can(role: str, action: str) -> None:
    if action not in CAN.get(role, ()):
        raise PermissionError(action)
```

Call this in `run_query` **after** the router returns `sql` or `etl`, and **before** `sql_node` / `etl_node`. A Viewer who is routed to ETL gets 403 and the ETL graph never starts.

Map `PermissionError` to HTTP 403 in the existing `HTTPException` handler (it already turns `HTTPException` into `ErrorResponse`). Raising `HTTPException(403)` from the route is enough if the check stays in `query_agent` and you pass the route in. Simplest place: inside `query_agent` is too early (you do not know the route yet). Check inside `run_query` and raise `HTTPException`.

```python
def run_query(question: str, user: CurrentUser) -> dict:
    result = data_agent.invoke({...})  # see 2.3 — pass user into state
    answer, route = answer_from_result(result)
    if route not in CAN[user.role]:
        raise HTTPException(status_code=403, detail=f"{user.role} cannot run {route}")
    return {"answer": answer, "route": route}
```

That check is late if the graph already ran ETL. So the graph itself must refuse. Add the same `assert_can` at the top of `etl_node` and `sql_node` in `agents/data_agent.py`, using `state` (next section). The HTTP layer repeats it only as a backstop.

## 2.3 Carry `tenant_id` into the agent

Add two optional fields on the agent state (`Models/schema.py`, `DataAgentSchema`): `tenant_id: str` and `role: str`. Default `""` so old CLI calls still construct.

`run_query` puts them on the invoke payload:

```python
result = data_agent.invoke(
    {
        "messages": [HumanMessage(content=question)],
        "route_response": "",
        "tenant_id": user.tenant_id,
        "role": user.role,
    }
)
```

`sql_node` forwards `tenant_id` into the SQL analyst input. The SQL tool does **not** string-interpolate it into the query. It sets a session variable, then runs the model’s SQL (section 2.4).

Also refuse an obvious cross-tenant ask in the app, before the LLM, when the question text names another tenant. This is a cheap extra, not the real boundary:

```python
import re

_OTHER = re.compile(r"tenant_id\s*=\s*'([^']+)'", re.I)

def mentions_other_tenant(question: str, tenant_id: str) -> bool:
    return any(found != tenant_id for found in _OTHER.findall(question))
```

If it matches, return 403. Row-level security still has to hold when the model writes `tenant_id = 'other'` on its own.

## 2.4 Postgres row-level security

App checks can be skipped by a bug. RLS cannot: the connection only sees rows for the tenant you set.

Every business table needs a `tenant_id` column. Run once as a table owner (not as the read-only agent role):

```sql
ALTER TABLE public.orders ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.orders FORCE ROW LEVEL SECURITY;

CREATE POLICY tenant_isolation ON public.orders
  USING (tenant_id = current_setting('app.tenant_id', true))
  WITH CHECK (tenant_id = current_setting('app.tenant_id', true));
```

Repeat per tenant table. `FORCE` applies the policy to the table owner too, so a mistaken superuser query in the app still filters. The agent’s DB user must **not** be a superuser and must **not** have `BYPASSRLS`.

In `execute_sql`, on the same connection, before the user query:

```python
cursor.execute("SELECT set_config('app.tenant_id', %s, true)", (tenant_id,))
cursor.execute(query)  # existing SELECT/WITH allowlist still runs first
```

`set_config(..., true)` is `SET LOCAL`: it dies at transaction end, so the next request cannot reuse another tenant’s value. Pass `tenant_id` into `execute_sql`. If it is missing, do not run the query.

`current_setting(..., true)` returns NULL when unset, and `tenant_id = NULL` is never true, so a forgotten `set_config` returns **no rows** instead of every row.

## 2.5 Columns — omit or mask, do not send secrets to the model

Keep a dict next to the role dict. Classify columns you actually have. Unknown columns are `internal` (visible, not masked).

```python
# level: public < internal < sensitive < highly_sensitive
COLUMNS = {
    "users.email": "sensitive",
    "users.phone": "highly_sensitive",
    "orders.amount": "internal",
}

_RANK = {"public": 0, "internal": 1, "sensitive": 2, "highly_sensitive": 3}
_MAX = {"admin": 3, "analyst": 2, "viewer": 1}  # viewer: public + internal only


def column_visible(role: str, table: str, column: str) -> bool:
    level = COLUMNS.get(f"{table}.{column}", "internal")
    return _RANK[level] <= _MAX[role]


def mask(role: str, table: str, column: str, value):
    level = COLUMNS.get(f"{table}.{column}", "internal")
    if role == "admin" or _RANK[level] <= 1:
        return value
    return None  # analyst: sensitive and highly_sensitive become null
```

Use `column_visible` in `schema_details` so a Viewer prompt never lists `users.phone`. Use `mask` on result cells before they are written into the final answer or the next LLM prompt. Phase 0 already removed sample rows; do not put them back.

## 2.6 Service API keys (optional, do this last)

Same `CurrentUser`. A key is a random secret you show once. Store only `sha256(key)` plus `tenant_id` and `role`. Compare with `hmac.compare_digest` on the hashes.

```python
import hashlib, hmac

def hash_key(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()

def key_matches(raw: str, stored_hex: str) -> bool:
    return hmac.compare_digest(hash_key(raw), stored_hex)
```

If the bearer token does not contain two dots (not a JWT), treat it as an API key and load that hash. On success, build `CurrentUser` from the stored row. Keys do not expire on their own: delete the row to revoke.

Skip this until a dashboard or job actually needs a non-interactive caller.

## Touch

- `api/auth.py` — new, token → `CurrentUser`
- `api/policy.py` — new, `CAN` + `COLUMNS`
- `api/routes.py` — `Depends(current_user)` on `POST /query`; pass user into `run_query`
- `agents/data_agent.py` — `tenant_id` and `role` on state; `assert_can` before SQL/ETL nodes
- `utils/database.py` — `set_config` then execute; mask or drop columns in schema + results
- `.env.example` — `JWT_SECRET`
- `pyproject.toml` — `pyjwt`
- `tests/test_api.py` — cases below (no live LLM, no live database)

## Tests

Mint tokens in the test with a fixed `JWT_SECRET`. Monkeypatch `run_query` the same way `test_query_returns_answer_and_route` already does.

- No header → 401
- Expired or bad signature → 401
- Token without `tenant_id` → 401
- Viewer + ETL route → 403, and the ETL node is not invoked
- Question containing `tenant_id = 'other'` while the token says `acme` → 403
- `/health` and `/ready` still 200 without a token

RLS itself is a SQL check, not a unit test: connect as the agent role, `set_config` to tenant A, `SELECT` a row that belongs to tenant B, expect zero rows.

## Done when

- [x] Unauthenticated `POST /api/v1/agent/query` returns 401
- [x] Viewer cannot call ETL or read forbidden columns
- [ ] A query for another `tenant_id` returns no rows (app check and RLS) — app check is in place; run [`sql/tenant_rls.sql`](../sql/tenant_rls.sql) once Postgres is up. `feed_db.py` applies that file on the next seed.
