# Production Implementation Roadmap

Phase-by-phase plan to take this tutorial **QueryMesh** (LangGraph SQL + ETL + Postgres) from demo to production-grade.

**Current state:** Good agent foundation. Not production-ready.  
**Goal:** Safe, reliable, observable, multi-user QueryMesh deployment you can deploy and defend in interviews.

---

## How to use this doc

| Column | Meaning |
|--------|---------|
| **What** | Concrete work to implement |
| **Why** | Risk or gap if you skip it |
| **Benefit** | What you gain after shipping it |

Suggested order: **Phase 0 → 6**. Do not skip Phase 0/1 for “features first” — security gaps (`exec()`, LLM-only SQL gate) are blockers.

---

## Phase 0 — Stabilize & stop the bleeding

**Theme:** Fix dangerous defaults before adding infrastructure.  
**Outcome:** Local demo is no longer trivially exploitable.

| # | What to implement | Why | Benefit |
|---|-------------------|-----|---------|
| 0.1 | Remove hardcoded DB credentials in `utils/database.py` (password `"potgres"` on import). Load only from `.env` / secret manager. | Secrets in code leak via git/screenshots; import-time connection is fragile. | Safer local setup; deployable config; no accidental credential commits. |
| 0.2 | Replace bare `exec(code)` in `utils/etl_tools.py` with a **temporary** hard block or allowlisted AST subset (no `os`, `subprocess`, `open` for writes, no network). | LLM-generated Python = remote code execution on your machine. | Immediate RCE risk reduced while real sandbox is built in Phase 3. |
| 0.3 | Add a **deterministic** SQL allowlist gate before `cursor.execute` (forbid `INSERT/UPDATE/DELETE/DROP/ALTER/TRUNCATE/CREATE`, require `SELECT`/`WITH` only). Keep LLM judge as secondary signal only. | LLM judge is jailbreakable; one bad SQL can wipe or mutate data. | Real security boundary; README “SQL safety” claim becomes true. |
| 0.4 | Enforce `statement_timeout`, `LIMIT`, and use a **read-only** DB user for agent queries. Stop `commit()` on read paths. | Expensive / mutating queries can hurt (or destroy) the DB. | Bounded blast radius; app DB stays healthier. |
| 0.5 | Stop sending sample table rows (PII) into schema prompts; send column metadata only. | Emails/phones in `users` already go into LLM context. | Less PII leakage; cheaper prompts; better privacy posture. |
| 0.6 | Align README with reality (remove or mark “planned”: sandbox, sensitive-data protection, rate limiting). | Overstated docs create false confidence. | Honest portfolio narrative; clearer backlog. |

**Exit criteria**

- [ ] No secrets in source
- [ ] No unrestricted `exec`
- [ ] SQL cannot run DML/DDL without explicit (later) approval path
- [ ] Schema prompts contain no sample PII rows

---

## Phase 1 — API foundation

**Theme:** Turn CLI `invoke` into a real service.  
**Outcome:** Versioned HTTP API with health, validation, and docs.

| # | What to implement | Why | Benefit |
|---|-------------------|-----|---------|
| 1.1 | **FastAPI** app wrapping `data_agent` (`POST /api/v1/agent/query`). | `main.py` is CLI-only; nothing to deploy or integrate with a UI. | Standard backend surface; frontend/mobile can call the agent. |
| 1.2 | Pydantic request/response + error schemas; OpenAPI auto-docs. | Ad-hoc dicts make clients and debugging painful. | Contract clarity; faster integration; swagger for demos. |
| 1.3 | API versioning (`/api/v1/...`). | Breaking changes will happen as agents evolve. | Clients can migrate without a hard cutover. |
| 1.4 | `GET /health` and `GET /ready` (API + DB + later Redis/LLM). | Orchestrators need liveness/readiness probes. | Safe rolling deploys; faster incident detection. |
| 1.5 | Request IDs on every call (middleware). | Without correlation IDs you cannot debug multi-step agent runs. | Trace one user question across logs/agents/SQL. |
| 1.6 | Basic structured logging (`request_id`, route, status, latency). | `print()` does not scale to production ops. | Searchable logs; baseline observability before full OTel. |

**Exit criteria**

- [ ] `uvicorn` serves the agent
- [ ] OpenAPI docs work
- [ ] Health endpoints report DB status
- [ ] Each request has a `request_id`

---

## Phase 2 — Auth, tenancy & access control

**Theme:** Who can ask what, against whose data.  
**Outcome:** Multi-user safe access with roles.

| # | What to implement | Why | Benefit |
|---|-------------------|-----|---------|
| 2.1 | JWT / OAuth2 authentication on API routes. | Anonymous access to QueryMesh is unacceptable for business data. | Only known users can query; foundation for audit. |
| 2.2 | RBAC (e.g. Admin / Analyst / Viewer) with permission checks before SQL/ETL. | Different roles need different table/column access. | Least privilege; enterprise interview story. |
| 2.3 | `tenant_id` on tokens + enforce in every query path. | Without tenancy, Company A can see Company B data. | Multi-tenant product readiness. |
| 2.4 | Postgres **Row-Level Security (RLS)** keyed by `tenant_id` (defense in depth). | App bugs alone are not enough isolation. | DB-enforced isolation even if agent SQL is wrong. |
| 2.5 | Column-level allowlists / PII classification (`PUBLIC` → `HIGHLY_SENSITIVE`) + masking. | Salary, phone, PAN must not leak to Viewers. | Compliance-friendly answers; fewer data incidents. |
| 2.6 | Service API keys for machine-to-machine callers (optional). | Dashboards/jobs need non-interactive auth. | Clean integration without shared user passwords. |

**Exit criteria**

- [ ] Unauthenticated requests rejected
- [ ] Role cannot access forbidden tables/columns
- [ ] Cross-tenant query attempts fail (app + RLS)

---

## Phase 3 — Safe execution & async work

**Theme:** Isolate dangerous work; don’t block HTTP on long ETL.  
**Outcome:** Sandboxed ETL + job queue.

| # | What to implement | Why | Benefit |
|---|-------------------|-----|---------|
| 3.1 | Real **Python sandbox** for ETL (container / isolated worker, CPU/mem/timeout, no host FS/network by default). | Phase 0 block is temporary; production ETL needs isolation. | LLM code cannot take over the API host. |
| 3.2 | URL allowlist / egress control for extract tools (kill open SSRF). | `requests.get(any_url)` can hit internal metadata services. | Network blast radius controlled. |
| 3.3 | **Redis** for session/cache + rate limiting. | Without limits, users burn LLM $; schema re-fetch is wasteful. | Cost control + faster repeated questions. |
| 3.4 | **Celery + RabbitMQ** (or equivalent) for ETL / long jobs: `POST /jobs` → job id → worker. | 10-minute ETL on a sync HTTP request times out and blocks workers. | Good UX; horizontal scale of heavy work. |
| 3.5 | `GET /jobs/{id}` (+ optional SSE/WebSocket progress). | Users need status without polling forever blindly. | Transparent long-running workflows. |
| 3.6 | Point agent at **analytics / read replica**, not primary app DB. | Bad joins can stall production OLTP. | Protects core product DB; safer experimentation. |

**Exit criteria**

- [ ] Generated Python never runs in API process
- [ ] Long ETL returns job id immediately
- [ ] Rate limit enforced per user/tenant
- [ ] Agent reads from analytics DB only

---

## Phase 4 — Agent quality: RAG, guardrails, cost

**Theme:** Better answers, safer prompts, lower spend.  
**Outcome:** Scalable schema retrieval + controlled LLM usage.

| # | What to implement | Why | Benefit |
|---|-------------------|-----|---------|
| 4.1 | **Schema RAG**: embed tables/columns; retrieve top-K relevant schema + relationships (not full dump). | Full schema dump fails at 100–1000 tables and wastes tokens. | Accurate SQL on large DBs; lower latency/cost. |
| 4.2 | Prompt-injection defenses (input guardrail, treat retrieved DB text as untrusted, output validation). | DB notes like “ignore instructions…” can hijack the agent. | Harder jailbreaks; safer tool use. |
| 4.3 | Model routing for real (env-driven models; cheap model for router, stronger for hard SQL). Wire what README already describes. | Hardcoded models + every step on a big model = expensive. | Lower cost; clearer complexity ladder. |
| 4.4 | Token budgets, max agent iterations, per-request cost tracking. | One request can trigger 5–6 LLM calls; loops burn money. | Predictable spend; kill runaway agents. |
| 4.5 | Cache schema retrieval + identical question answers (Redis). | Repeated questions re-pay LLM + DB. | Faster UX; material cost savings. |
| 4.6 | Human-in-the-loop for high-risk actions (expensive query, sensitive columns, any write). | Auto-execute is wrong for high blast-radius ops. | Governance without blocking all queries. |

**Exit criteria**

- [ ] Only top-K schema fragments sent to LLM
- [ ] Injection test cases fail closed
- [ ] Cost per request visible in logs/metrics
- [ ] High-risk path requires approval

---

## Phase 5 — Observability, reliability & evaluation

**Theme:** Operate and improve the system with evidence.  
**Outcome:** You can debug, measure, and regress-test the agent.

| # | What to implement | Why | Benefit |
|---|-------------------|-----|---------|
| 5.1 | OpenTelemetry tracing across API → router → SQL/ETL → DB/tools. | “Why did this take 18s?” needs spans, not guesses. | Fast root-cause; clear bottleneck maps. |
| 5.2 | LangSmith or Langfuse for prompts, tokens, tool calls. | Agent-specific debugging needs LLM-aware traces. | Prompt iteration with data, not vibes. |
| 5.3 | Metrics: request count, error rate, P50/P95 latency, tokens, SQL time, success rate. | Without SLOs you cannot run production. | Alerts; capacity planning; demo dashboards. |
| 5.4 | Audit log: `user_id`, `tenant_id`, question, SQL, tools, status (immutable store). | Enterprises need “who queried what”. | Compliance + forensics. |
| 5.5 | Retries with backoff, timeouts, fallback models, graceful errors; circuit breakers for LLM/DB. | LLMs and DBs fail; infinite loops happen. | Higher uptime; controlled degradation. |
| 5.6 | Test pyramid: unit (SQL validator, RBAC), integration (API→agent→DB), security (DROP / injection / cross-tenant). | Zero tests today = every change is a gamble. | Confident refactors; catch regressions early. |
| 5.7 | Evaluation harness: 100–500 business questions → SQL accuracy, tool routing, groundedness, latency, cost. | “It answered” ≠ “it answered correctly”. | Measurable quality bar; resume-grade AI eng story. |

**Exit criteria**

- [ ] One request produces a full trace
- [ ] Dashboards for latency/cost/errors
- [ ] CI runs unit + security tests
- [ ] Eval suite runs offline and reports scores

---

## Phase 6 — Deploy like production

**Theme:** Ship and run it.  
**Outcome:** Reproducible deploys with CI/CD.

| # | What to implement | Why | Benefit |
|---|-------------------|-----|---------|
| 6.1 | Dockerfile for API + worker; `docker-compose` for API, Postgres, Redis, RabbitMQ. | “Works on my laptop” is not a deployment plan. | One-command local prod-like stack. |
| 6.2 | CI (GitHub Actions): lint, test, build image, (optional) eval smoke. | Broken main branch wastes everyone. | Quality gate before merge. |
| 6.3 | Push images to registry; deploy to ECS/EKS/Cloud Run (or similar) behind a load balancer. | Portfolio needs a real deploy story. | Public/demo URL; cloud ops experience. |
| 6.4 | Secrets via cloud secret manager (not `.env` in prod). | Env files on VMs get leaked. | Rotatable secrets; audit access. |
| 6.5 | Backups + restore drill for analytics Postgres. | Agent-facing DBs still need recovery. | Survive mistakes and outages. |
| 6.6 | Feedback API (`POST /api/v1/feedback`) wired into eval/prompt iteration. | Users teach you where answers fail. | Closed-loop improvement. |

**Exit criteria**

- [ ] `docker compose up` runs the stack
- [ ] CI green on PR
- [ ] Deployed environment with health checks passing
- [ ] Secrets not in images or git

---

## Phase summary (at a glance)

| Phase | Focus | Rough effort* | Unlocks |
|-------|--------|---------------|---------|
| **0** | Safety hotfixes | 2–4 days | Safe to keep developing |
| **1** | FastAPI + health | 3–5 days | Real product surface |
| **2** | Auth / RBAC / tenancy | 1–2 weeks | Multi-user enterprise path |
| **3** | Sandbox + queue + Redis | 1–2 weeks | Safe ETL at scale |
| **4** | RAG + guardrails + cost | 1–2 weeks | Better answers, lower $ |
| **5** | Obs + tests + eval | 1–2 weeks | Operable + measurable |
| **6** | Docker + CI/CD + cloud | 1 week | Demoable production deploy |

\*Solo developer estimates; shrink if you cut scope.

---

## Resume-priority 12 (if time is limited)

Ship these in order; skip optional polish until these exist:

1. FastAPI  
2. Analytics / read-only Postgres  
3. LangGraph multi-agent (already present — harden)  
4. Schema RAG  
5. SQL validation + read-only execution  
6. RBAC + tenant isolation  
7. Redis  
8. RabbitMQ/Celery for ETL  
9. Python sandbox  
10. Langfuse/LangSmith + OpenTelemetry  
11. Automated agent evaluation  
12. Docker + CI/CD (+ cloud)

That set covers: **LLMs + agents + RAG + databases + APIs + distributed systems + security + cloud + observability + testing**.

---

## Mapping: your production layers → phases

| Production layer | Phase |
|------------------|-------|
| Auth & RBAC | 2 |
| SQL security | 0, then deepen in 2–3 |
| Prompt injection | 4 |
| PII protection | 0 + 2 |
| Multi-tenancy | 2 |
| Async jobs / queue | 3 |
| Python sandbox | 0 (temp) → 3 (real) |
| Observability | 1 (basic) → 5 (full) |
| LLM cost management | 4 |
| Reliability | 5 |
| Testing | 5 |
| Evaluation | 5 |
| Schema RAG | 4 |
| DB architecture (replica) | 3 |
| Rate limiting | 3 |
| Secrets | 0 + 6 |
| Deployment | 6 |
| Health checks | 1 |
| API docs / versioning | 1 |
| Human-in-the-loop | 4 |
| Audit logs | 5 |
| Caching | 3–4 |

---

## Recommended next step

Start **Phase 0** immediately (credentials, SQL gate, kill `exec`, strip PII from schema prompts), then **Phase 1** FastAPI.

When Phase 0 + 1 are done, the project is already a credible “secure API-backed QueryMesh” story — then add tenancy, queues, and eval for depth.
