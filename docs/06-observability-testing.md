# 06 — Observability, tests, and eval

**Goal:** A failed answer can be traced, and a regression fails CI.

There are no tests in the repo today.

## Build

1. OpenTelemetry spans: API → router → SQL or ETL → database or tool. Propagate `request_id`.
2. LLM trace export (Langfuse or LangSmith): prompt, model, tokens, tool calls. Keys from env only.
3. Metrics: request count, error rate, latency percentiles, token totals, SQL time, route (`sql` vs `etl`).
4. Audit record: user, tenant, question, generated SQL, tools, status. Append-only.
5. Timeouts, retries with backoff, and a fallback model when the primary LLM errors. No unbounded tool loops.
6. Tests:
   - Unit: SQL allowlist rejects DML/DDL; path jail; role checks.
   - Integration: API → agent → Postgres on a fixture database.
   - Security: `DROP`, stacked queries, cross-tenant, SSRF URL.
7. Offline eval set (start with ~50 questions, grow later): route accuracy, SQL execution success, grounded answer. Report score, latency, and cost.

## Done when

- [ ] One query produces a single trace from HTTP to SQL
- [ ] `pytest` covers the SQL gate and auth rejection
- [ ] CI (see [07-deployment.md](./07-deployment.md)) runs those tests
- [ ] Eval script prints a score without calling production
