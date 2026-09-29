"""Row dump of public tables, plus a restore drill onto a second database.

`python -m utils.pg_backup` writes `backups/<database>.sql`, loads the marker
table into `<database>_restore`, and checks the row. Table DDL stays in SQL files;
this file is data. A full schema backup is `pg_dump` from the Postgres image.
"""

import os
import sys
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

import psycopg2
from dotenv import load_dotenv

_ROOT = Path(__file__).resolve().parents[1]
_NOTE = "phase6-restore-drill"


def ident(name: str) -> str:
    if not name.replace("_", "").isalnum():
        raise ValueError(f"unsafe identifier: {name}")
    return '"' + name + '"'


def literal(value) -> str:
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, (int, Decimal)):
        return str(value)
    if isinstance(value, float):
        return repr(value)
    if isinstance(value, (datetime, date)):
        return "'" + value.isoformat().replace("'", "''") + "'"
    return "'" + str(value).replace("'", "''") + "'"


def render_dump(tables: list[dict]) -> str:
    lines = ["BEGIN;"]
    for table in tables:
        name = ident(table["name"])
        columns = [ident(column) for column in table["columns"]]
        column_sql = ", ".join(columns)
        lines.append(f"DELETE FROM {name};")
        for row in table["rows"]:
            if len(row) != len(columns):
                raise ValueError(f"{table['name']} row width does not match columns")
            values = ", ".join(literal(item) for item in row)
            lines.append(f"INSERT INTO {name} ({column_sql}) VALUES ({values});")
    lines.append("COMMIT;")
    return "\n".join(lines) + "\n"


def marker_script(script: str) -> str:
    """Statements for backup_drill only, so an empty copy does not need every table."""
    kept = ["BEGIN;"]
    for line in script.splitlines():
        if '"backup_drill"' in line and line.startswith(("DELETE FROM ", "INSERT INTO ")):
            kept.append(line)
    kept.append("COMMIT;")
    return "\n".join(kept) + "\n"


def admin_config() -> dict:
    load_dotenv()
    host = os.environ.get("BACKUP_HOST", os.environ.get("host", "")).strip()
    port = os.environ.get("BACKUP_PORT", os.environ.get("port", "")).strip()
    user = os.environ.get("BACKUP_USER", os.environ.get("user", "")).strip()
    database = os.environ.get("BACKUP_DATABASE", os.environ.get("database", "")).strip()
    password = os.environ.get("BACKUP_PASSWORD", os.environ.get("password", ""))
    if not all((host, port, user, database)):
        raise RuntimeError("BACKUP_HOST, BACKUP_PORT, BACKUP_USER, and BACKUP_DATABASE are required")
    return {
        "host": host,
        "port": int(port),
        "user": user,
        "password": password,
        "dbname": database,
    }


def _public_tables(connection) -> list[dict]:
    cursor = connection.cursor()
    cursor.execute(
        """
        SELECT table_name
        FROM information_schema.tables
        WHERE table_schema = 'public' AND table_type = 'BASE TABLE'
        ORDER BY table_name
        """
    )
    tables = []
    for (name,) in cursor.fetchall():
        if not name.replace("_", "").isalnum():
            continue
        cursor.execute(
            """
            SELECT column_name
            FROM information_schema.columns
            WHERE table_schema = 'public' AND table_name = %s
            ORDER BY ordinal_position
            """,
            (name,),
        )
        columns = [row[0] for row in cursor.fetchall()]
        if any(not column.replace("_", "").isalnum() for column in columns):
            continue
        quoted = ", ".join(ident(column) for column in columns)
        cursor.execute(f"SELECT {quoted} FROM {ident(name)}")
        tables.append({"name": name, "columns": columns, "rows": cursor.fetchall()})
    cursor.close()
    return tables


def apply_script(cursor, script: str) -> None:
    """Run our one-statement-per-line dump. psycopg2 executes a single statement at a time."""
    for line in script.splitlines():
        statement = line.strip()
        if not statement or statement in {"BEGIN;", "COMMIT;"}:
            continue
        if statement.endswith(";"):
            statement = statement[:-1]
        cursor.execute(statement)


def drill(dump_path: Path | None = None) -> str:
    """Write a row dump, restore backup_drill onto a second database, return the note."""
    cfg = admin_config()
    source_name = cfg["dbname"]
    restore_name = source_name + "_restore"
    ident(restore_name)

    source = psycopg2.connect(**cfg)
    source.autocommit = True
    cursor = source.cursor()
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS public.backup_drill (
            id integer PRIMARY KEY,
            note text NOT NULL
        )
        """
    )
    cursor.execute(
        "INSERT INTO public.backup_drill (id, note) VALUES (1, %s) "
        "ON CONFLICT (id) DO UPDATE SET note = EXCLUDED.note",
        (_NOTE,),
    )
    cursor.close()
    script = render_dump(_public_tables(source))
    source.close()

    path = dump_path or (_ROOT / "backups" / f"{source_name}.sql")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(script, encoding="utf-8")

    admin = psycopg2.connect(**{**cfg, "dbname": "postgres"})
    admin.autocommit = True
    cursor = admin.cursor()
    cursor.execute(
        "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = %s AND pid <> pg_backend_pid()",
        (restore_name,),
    )
    cursor.execute(f"DROP DATABASE IF EXISTS {ident(restore_name)}")
    cursor.execute(f"CREATE DATABASE {ident(restore_name)}")
    cursor.close()
    admin.close()

    restored = psycopg2.connect(**{**cfg, "dbname": restore_name})
    restored.autocommit = True
    cursor = restored.cursor()
    cursor.execute(
        """
        CREATE TABLE public.backup_drill (
            id integer PRIMARY KEY,
            note text NOT NULL
        )
        """
    )
    apply_script(cursor, marker_script(path.read_text(encoding="utf-8")))
    cursor.execute("SELECT note FROM public.backup_drill WHERE id = 1")
    row = cursor.fetchone()
    cursor.close()
    restored.close()
    if row is None or row[0] != _NOTE:
        raise RuntimeError("restore drill failed: marker row missing")
    return row[0]


def main() -> int:
    print(f"restore drill ok: {drill()}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"restore drill failed: {exc}", file=sys.stderr)
        raise SystemExit(1)
