# 05 — Agent quality and cost

**Goal:** Answers stay accurate as the schema grows, and one request cannot run away on tokens.

Depends on [01-security.md](./01-security.md).

## Build

1. **Schema RAG.** Embed table and column descriptions. Retrieve top-K relevant tables instead of dumping `information_schema` for every question (`schema_details` today loads every table).
2. **Guardrails.** Treat user text and database text as untrusted. Cap agent steps. Validate SQL again after generation (phase 0 gate). Reject outputs that ignore the system instruction.
3. **Model routing.** Drive model names from env. Router uses the cheap model (`pick_llm("low")`). SQL generation uses a stronger model only when needed. Remove placeholder model ids that are not actually available.
4. **Budgets.** Max tokens and max graph steps per request. Log token use and estimated cost on the request id.
5. **Cache.** Cache schema retrieval and identical question results in Redis with a short TTL.
6. **Approval.** High-risk actions (sensitive columns, large scans, any future write) return `needs_approval` instead of executing.

## Done when

- [ ] Prompts contain only the retrieved schema slice
- [ ] A prompt-injection fixture fails closed
- [ ] Logs show tokens and cost for a request
- [ ] Over-budget runs stop with a clear error
