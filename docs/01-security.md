# 01 — Security hotfixes

**Goal:** Local demo is no longer trivially exploitable.  
**Do this before any API or deploy work.**

## Fix

1. **Secrets.** Delete the module-level `DatabaseUtil({... password: "potgres" ...})` block and the `test_schema_details.txt` write in `utils/database.py`. Load host, port, user, password, database from environment only. Fail at startup if a required key is missing. Add `test_schema_details.txt` to `.gitignore`.
2. **SQL gate.** Before `cursor.execute` in `execute_sql`, accept only a single statement that starts with `SELECT` or `WITH`. Reject `INSERT`, `UPDATE`, `DELETE`, `DROP`, `ALTER`, `TRUNCATE`, `CREATE`, `GRANT`, `COPY`, comments used to hide a second statement, and multiple statements. The LLM judge in `agents/sql_analyst.py` stays a secondary signal.
3. **Read-only execution.** Use a Postgres role with `SELECT` only. Set `statement_timeout`. Append or enforce a row `LIMIT`. Remove `connection.commit()` on the read path.
4. **No sample PII in prompts.** `schema_details` should return table and column metadata only. Drop the `SELECT * ... LIMIT 5` block.
5. **Stop open `exec`.** `ETLTools.execute_code` must not call `exec` on model output. Return an error until the sandbox in [04-safe-execution.md](./04-safe-execution.md) exists.
6. **Docs honesty.** README already calls this a tutorial. Keep that sentence until the SQL gate and sandbox exist.

## Touch

- `utils/database.py`
- `utils/etl_tools.py`
- `agents/sql_analyst.py` (pass env config, do not construct a second hardcoded connection)
- `.gitignore`
- `.env.example` (document the read-only role; do not put real secrets)

## Done when

- [x] Importing `utils.database` does not connect or write a file
- [x] `DROP`, `DELETE`, and stacked statements are rejected before the driver
- [x] Schema context contains column names and types only
- [x] `execute_code` cannot run arbitrary Python
