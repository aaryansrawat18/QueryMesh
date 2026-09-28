import psycopg2
from psycopg2 import sql

from api.policy import column_visible, mask_cell


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

                if not visible:
                    continue

                sample_query = sql.SQL("SELECT {} FROM {}.{} LIMIT 5").format(
                    sql.SQL(", ").join(sql.Identifier(name) for name, _ in visible),
                    sql.Identifier(schema_name),
                    sql.Identifier(table_name),
                )
                cursor.execute(sample_query)
                sample_data = cursor.fetchall()
                schema_info_context = f"{schema_info_context}  Sample Data:\n"
                for row in sample_data:
                    schema_info_context = f"{schema_info_context}    {row}\n"

        except Exception as e:
            print(f"Error fetching schema details: {e}")
            schema_info_context = f"Error fetching schema details: {e}"

        finally:
            if cursor:
                cursor.close()
            if connection:
                connection.close()

        return schema_info_context

    def execute_sql(self, query, tenant_id: str = "", role: str = ""):
        if not tenant_id:
            raise ValueError("tenant_id required")
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
            connection.commit()
            return str(masked)
        except Exception as e:
            print(f"Error executing query: {e}")
            return None
        finally:
            if cursor:
                cursor.close()
            if connection:
                connection.close()


if __name__ == "__main__":
    # ponytail: do not connect on import — GET /ready probes Postgres.
    obj = DatabaseUtil({
        "host": "localhost",
        "port": 5432,
        "user": "postgres",
        "password": "potgres",
        "dbname": "postgres"
    })

    result = obj.schema_details("public")

    with open("test_schema_details.txt", "w") as f:
        f.write(result)