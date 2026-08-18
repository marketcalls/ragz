#!/bin/sh
# Runs ONCE, when the pgdata volume is first initialised.
#
# A shell script, not plain .sql, because the app role's password comes from the
# environment: docker-entrypoint-initdb.d runs .sql files through psql with no
# variables set, so a .sql file could only hardcode the secret.
#
# POSTGRES_USER (ragz) owns the schema and is used ONLY by the one-shot migrate
# job, which needs DDL. Everything that serves traffic connects as ragz_app,
# which cannot create, alter or drop anything, so a compromised API or worker
# cannot drop the schema it reads.
set -eu

: "${RAGZ_APP_DB_PASSWORD:?set RAGZ_APP_DB_PASSWORD}"

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<SQL
-- LiteLLM keeps its own model store. A separate DATABASE, not a schema inside
-- ragz: its migrations own their database, and mixing a third party's DDL into
-- the application's database is how they end up entangled.
CREATE DATABASE litellm;

-- Runtime role: no CREATEDB, no CREATEROLE, no superuser. NOINHERIT so it
-- cannot pick up the owner's rights through membership.
CREATE ROLE ragz_app LOGIN NOINHERIT PASSWORD '${RAGZ_APP_DB_PASSWORD}';

GRANT CONNECT ON DATABASE ${POSTGRES_DB} TO ragz_app;
GRANT USAGE ON SCHEMA public TO ragz_app;

-- DML only. No CREATE on the schema, so the app cannot add tables; no TRUNCATE,
-- which is irreversible and closer to DDL than to a delete.
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO ragz_app;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO ragz_app;

-- Tables created by LATER migrations must inherit the same grants, or each
-- migration would silently lock the app out of the tables it just added.
ALTER DEFAULT PRIVILEGES FOR ROLE ${POSTGRES_USER} IN SCHEMA public
    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO ragz_app;
ALTER DEFAULT PRIVILEGES FOR ROLE ${POSTGRES_USER} IN SCHEMA public
    GRANT USAGE, SELECT ON SEQUENCES TO ragz_app;
SQL
