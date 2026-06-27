-- mcplookup_app_role.sql  (idempotent)  -- APPLIED + VERIFIED on Fly Postgres 2026-06-27.
-- Least-privilege login role for the MCPLookup web app, SEPARATE from the owner role.
-- Read-everywhere; write only on the app-owned identity/usage/cache tables; INSERT-only
-- on the registry (user submissions); the moat (mcp_llm_axis_scores) is READ-ONLY.
-- NOTE: the Fly Managed Postgres database is named "mcplookup" (not zo_sentinel).
-- Apply as the DB OWNER:  psql "$OWNER_DSN" -v app_pw='<pw>' -f mcplookup_app_role.sql
\set ON_ERROR_STOP on

DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='mcplookup_app') THEN
    EXECUTE format('CREATE ROLE mcplookup_app LOGIN PASSWORD %L', :'app_pw');
  ELSE
    EXECUTE format('ALTER ROLE mcplookup_app LOGIN PASSWORD %L', :'app_pw');
  END IF;
END$$;

GRANT CONNECT ON DATABASE "mcplookup" TO mcplookup_app;
GRANT USAGE  ON SCHEMA public         TO mcplookup_app;
GRANT SELECT ON ALL TABLES    IN SCHEMA public TO mcplookup_app;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO mcplookup_app;

-- WRITE: app-owned identity (Clerk provisioning), usage counter, dashboard cache
GRANT INSERT, UPDATE, DELETE ON orgs, users, api_keys, api_usage, app_stats TO mcplookup_app;
-- Registry: user submissions INSERT new unreviewed rows; never edit/delete scored rows
GRANT INSERT ON mcp_server_registry TO mcplookup_app;

ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT        ON TABLES    TO mcplookup_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT USAGE, SELECT ON SEQUENCES TO mcplookup_app;

-- Moat is read-only to the app; registry is insert-only; no object creation.
REVOKE INSERT, UPDATE, DELETE, TRUNCATE ON mcp_llm_axis_scores FROM mcplookup_app;
REVOKE UPDATE, DELETE, TRUNCATE         ON mcp_server_registry  FROM mcplookup_app;
REVOKE CREATE ON SCHEMA public FROM mcplookup_app;
