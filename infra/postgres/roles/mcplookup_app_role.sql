-- mcplookup_app_role.sql  (idempotent)
-- Least-privilege login role for the MCPLookup web app, SEPARATE from the
-- owner/write_service role. Principle: the app READS the whole intel surface
-- (scores, registry, signals, verdicts) but WRITES only its own identity tables
-- (Clerk org/user/key provisioning). The moat tables stay read-only to the app.
--
-- Apply as the DATABASE OWNER (e.g. zo) against the zo_sentinel DB, on BOTH the
-- tower Postgres and the Fly Managed Postgres:
--   psql "$OWNER_DSN" -v app_pw='<strong-password>' -f mcplookup_app_role.sql

\set ON_ERROR_STOP on

DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'mcplookup_app') THEN
    EXECUTE format('CREATE ROLE mcplookup_app LOGIN PASSWORD %L', :'app_pw');
  ELSE
    EXECUTE format('ALTER ROLE mcplookup_app LOGIN PASSWORD %L', :'app_pw');
  END IF;
END$$;

-- Connect + schema usage
GRANT CONNECT ON DATABASE zo_sentinel TO mcplookup_app;
GRANT USAGE  ON SCHEMA public         TO mcplookup_app;

-- READ everything: the app is a read-mostly analytics surface over the registry.
GRANT SELECT ON ALL TABLES    IN SCHEMA public TO mcplookup_app;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO mcplookup_app;

-- WRITE only on app-owned identity tables (Clerk org/user provisioning + API keys).
GRANT INSERT, UPDATE, DELETE ON orgs, users, api_keys TO mcplookup_app;

-- Future owner-created tables (e.g. new write_service intel tables) auto-readable,
-- never writable by the app.
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT          ON TABLES    TO mcplookup_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT USAGE, SELECT   ON SEQUENCES TO mcplookup_app;

-- Defensive: the moat tables are read-only to the app even though app/models.py
-- maps them (the ORM mapping exists for READS; the scorer/write_service owns writes).
REVOKE INSERT, UPDATE, DELETE, TRUNCATE
  ON mcp_llm_axis_scores, mcp_server_registry
  FROM mcplookup_app;

-- Never let the app create/own objects.
REVOKE CREATE ON SCHEMA public FROM mcplookup_app;
