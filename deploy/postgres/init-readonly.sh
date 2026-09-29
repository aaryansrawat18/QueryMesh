#!/bin/sh
# Local compose only. Production roles are created in the managed database.
pass=$(printf '%s' "$ANALYTICS_PASSWORD" | sed "s/'/''/g")
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<EOSQL
DO \$body\$
BEGIN
   IF NOT EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname = 'querymesh_ro') THEN
      CREATE ROLE querymesh_ro LOGIN PASSWORD '${pass}';
   END IF;
END
\$body\$;
GRANT CONNECT ON DATABASE "${POSTGRES_DB}" TO querymesh_ro;
GRANT USAGE ON SCHEMA public TO querymesh_ro;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO querymesh_ro;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO querymesh_ro;
ALTER ROLE querymesh_ro SET default_transaction_read_only = on;
EOSQL
