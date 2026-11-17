#!/usr/bin/env bash
# Create the database roles, the database and extensions (DEPLOYMENT.md §2.3).
# Run once per new PostgreSQL cluster/database as the Postgres superuser (PG* env vars or socket).
# Idempotent: re-running is safe and re-syncs role passwords from the environment.
set -euo pipefail

: "${REBATE_OWNER_PASSWORD:?must be set}"
: "${REBATE_APP_PASSWORD:?must be set}"
: "${REBATE_AI_WRITER_PASSWORD:?must be set}"
: "${REBATE_READONLY_PASSWORD:?must be set}"
DB="${REBATE_DB_NAME:-${REBATE_DB:-rebate}}"

psql -X -v ON_ERROR_STOP=1 -v db="$DB" --dbname postgres <<'SQL'
\getenv owner_pw REBATE_OWNER_PASSWORD
\getenv app_pw REBATE_APP_PASSWORD
\getenv ai_pw REBATE_AI_WRITER_PASSWORD
\getenv ro_pw REBATE_READONLY_PASSWORD

SELECT format('CREATE ROLE %I', r)
FROM unnest(ARRAY['rebate_owner', 'rebate_app', 'rebate_ai_writer', 'rebate_readonly']) AS r
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = r) \gexec

ALTER ROLE rebate_owner     LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS PASSWORD :'owner_pw';
ALTER ROLE rebate_app       LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS PASSWORD :'app_pw';
ALTER ROLE rebate_ai_writer LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS PASSWORD :'ai_pw';
ALTER ROLE rebate_readonly  LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS PASSWORD :'ro_pw';

-- Owner of the SECURITY DEFINER cross-tenant lookups only (DATABASE_SCHEMA.md §2). Cannot log in.
-- rebate_owner may SET ROLE to it (needed to hand over function ownership) but does not inherit it.
SELECT 'CREATE ROLE rebate_lookup' WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'rebate_lookup') \gexec
ALTER ROLE rebate_lookup NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION BYPASSRLS;
GRANT rebate_lookup TO rebate_owner WITH INHERIT FALSE, SET TRUE;

SELECT format('CREATE DATABASE %I OWNER rebate_owner', :'db')
WHERE NOT EXISTS (SELECT 1 FROM pg_database WHERE datname = :'db') \gexec

REVOKE ALL ON DATABASE :"db" FROM PUBLIC;
GRANT CONNECT ON DATABASE :"db" TO rebate_app, rebate_ai_writer, rebate_readonly;

\connect :"db"
CREATE EXTENSION IF NOT EXISTS btree_gist;
CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE EXTENSION IF NOT EXISTS citext;
SQL

echo "init-db: roles, database '$DB' and extensions are in place"
