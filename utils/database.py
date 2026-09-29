import os
import time

import psycopg2
from dotenv import load_dotenv

from api.policy import column_visible, mask_cell
from utils.metrics import metrics
from utils.reliability import CircuitOpen, call, db_breaker
from utils.sql_gate import assert_read_only
from utils.trace import span


def analytics_config() -> dict:
    """Read-replica settings. The agent never uses the primary host/user/database keys."""
    load_dotenv()
    host = os.environ.get("ANALYTICS_HOST", "").strip()
    port = os.environ.get("ANALYTICS_PORT", "").strip()
    user = os.environ.get("ANALYTICS_USER", "").strip()
    database = os.environ.get("ANALYTICS_DATABASE", "").strip()
    if not all((host, port, user, database)):
        raise RuntimeError("ANALYTICS_HOST, ANALYTICS_PORT, ANALYTICS_USER, and ANALYTICS_DATABASE are required")
    timeout_ms = int(os.environ.get("SQL_STATEMENT_TIMEOUT_MS", "5000"))
    config = {
        "host": host,
        "port": int(port),
        "user": user,
        "password": os.environ.get("ANALYTICS_PASSWORD", ""),
        "dbname": database,
        "options": f"-c statement_timeout={timeout_ms}",
    }
    sslmode = os.environ.get("ANALYTICS_SSLMODE", "").strip()
    if sslmode:
        config["sslmode"] = sslmode
    return config


class DatabaseUtil:

    def __init__(self, db_config):
        self.db_config = db_config

        try: 
            self.connection = psycopg2.connect(**db_config) 

        except Exception as e:
            print(f"Error connecting to the database: {e}")
            self.connection = None

    def schema_details(self, schema_name, role: str = ""):

        schema_info_context = ""
        connection = self.connection
        cursor = None

        schema_info_context = f"Database Schema: {schema_name}\n"

        try:
            cursor = connection.cursor()
            cursor.execute(
                "SELECT table_name from information_schema.tables where table_schema = %s;",
                (schema_name,),
            )
            tables_list = cursor.fetchall()

            for table in tables_list:
                table_name = table[0]
                schema_info_context = f"{schema_info_context}\nTable: {table_name}\n"

                cursor.execute(
                    "SELECT column_name, data_type FROM information_schema.columns WHERE table_schema = %s AND table_name = %s;",
                    (schema_name, table_name),
                )
                columns_list = cursor.fetchall()
                visible = [
                    (column_name, data_type)
                    for column_name, data_type in columns_list
                    if column_visible(role, table_name, column_name)
                ]

                for column_name, data_type in visible:
                    schema_info_context = f"{schema_info_context}  Column: {column_name}, Data Type: {data_type}\n"

        except Exception as e:
            print(f"Error fetching schema details: {e}")
            schema_info_context = f"Error fetching schema details: {e}"

        finally:
            if cursor:
                cursor.close()
            if connection:
                connection.close()

        return schema_info_context

    def catalog(self, schema_name, role: str = "") -> list[dict]:
        """Table and column metadata plus foreign keys. No sample rows."""
        if self.connection is None:
            return []
        cursor = None
        try:
            cursor = self.connection.cursor()
            cursor.execute(
                "SELECT table_name FROM information_schema.tables WHERE table_schema = %s;",
                (schema_name,),
            )
            tables = [row[0] for row in cursor.fetchall()]
            refs: dict[str, list[tuple[str, str, str]]] = {}
            try:
                cursor.execute(
                    """
                    SELECT tc.table_name, kcu.column_name, ccu.table_name, ccu.column_name
                    FROM information_schema.table_constraints tc
                    JOIN information_schema.key_column_usage kcu
                      ON tc.constraint_name = kcu.constraint_name
                     AND tc.table_schema = kcu.table_schema
                    JOIN information_schema.constraint_column_usage ccu
                      ON ccu.constraint_name = tc.constraint_name
                     AND ccu.table_schema = tc.table_schema
                    WHERE tc.constraint_type = 'FOREIGN KEY' AND tc.table_schema = %s
                    """,
                    (schema_name,),
                )
                for table_name, column_name, ref_table, ref_column in cursor.fetchall():
                    refs.setdefault(table_name, []).append((column_name, ref_table, ref_column))
            except Exception:
                self.connection.rollback()
                cursor = self.connection.cursor()
            docs = []
            for table_name in tables:
                cursor.execute(
                    "SELECT column_name, data_type FROM information_schema.columns WHERE table_schema = %s AND table_name = %s;",
                    (schema_name, table_name),
                )
                visible = [
                    (column_name, data_type)
                    for column_name, data_type in cursor.fetchall()
                    if column_visible(role, table_name, column_name)
                ]
                if not visible:
                    continue
                columns = ", ".join(f"{name} {data_type}" for name, data_type in visible)
                links = refs.get(table_name, [])
                relation = ""
                if links:
                    rendered = "; ".join(
                        f"{table_name}.{column} references {ref_table}.{ref_column}"
                        for column, ref_table, ref_column in links
                    )
                    relation = f" Relationships: {rendered}."
                docs.append(
                    {
                        "name": table_name,
                        "text": f"Table {table_name}. Columns: {columns}.{relation}",
                        "refs": [ref_table for _column, ref_table, _ref_column in links],
                    }
                )
            return docs
        except Exception as exc:
            print(f"Error fetching schema catalog: {exc}")
            return []
        finally:
            if cursor:
                cursor.close()
            if self.connection:
                self.connection.close()

    def _query(self, query, tenant_id: str, role: str):
        if self.connection is None or getattr(self.connection, "closed", 0):
            self.connection = psycopg2.connect(**self.db_config)
        cursor = None
        connection = self.connection
        try:
            cursor = connection.cursor()
            cursor.execute("SELECT set_config('app.tenant_id', %s, true)", (tenant_id,))
            cursor.fetchone()
            cursor.execute(query)
            columns = [desc[0] for desc in cursor.description] if cursor.description else []
            rows = cursor.fetchall()
            masked = [
                tuple(
                    mask_cell(role or "viewer", columns[index] if index < len(columns) else "", value)
                    for index, value in enumerate(row)
                )
                for row in rows
            ]
            return str(masked)
        finally:
            if cursor:
                cursor.close()
            if connection:
                connection.close()

    def execute_sql(self, query, tenant_id: str = "", role: str = ""):
        if not tenant_id:
            raise ValueError("tenant_id required")
        query = assert_read_only(query)
        started = time.perf_counter()
        try:
            with span("db.execute"):
                result = call(
                    db_breaker,
                    lambda: self._query(query, tenant_id, role),
                    attempts=int(os.environ.get("DB_RETRIES", "2")),
                    retryable=lambda exc: isinstance(exc, (psycopg2.OperationalError, psycopg2.InterfaceError)),
                )
        except CircuitOpen:
            raise
        except Exception as exc:
            print(f"Error executing query: {exc}")
            return "query failed"
        metrics.observe_sql((time.perf_counter() - started) * 1000)
        return result