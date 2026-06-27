# Least-privilege app DB role (`mcplookup_app`)

The MCPLookup web app must NOT share the owner/`write_service` role. This role is
**read-everywhere, write only on the identity tables** (`orgs`, `users`, `api_keys`).
The scored-registry tables (`mcp_llm_axis_scores`, `mcp_server_registry`, and all
signal/verdict tables) are **read-only** to the app — the scorer/write_service owns
those writes. That keeps a web-tier bug or compromise from corrupting the moat.

## Why a separate role doesn''t break migrations
`alembic upgrade head` needs DDL, which this role deliberately lacks. So:
- **App runtime + reads** use `DATABASE_URL` = the `mcplookup_app` DSN (least-priv).
- **Migrations** run as the OWNER. No code change needed — override the env var for
  just the release command:

  ```toml
  # fly.toml
  [deploy]
    release_command = "sh -lc 'DATABASE_URL=\"$OWNER_DATABASE_URL\" alembic upgrade head'"
  ```

  `OWNER_DATABASE_URL` is a second Fly secret holding the owner DSN.

## Apply (tower + Fly), as the owner
```bash
# 1. Pick a strong password; store it in AgentVault (DPAPI keyring), not in a file:
#    python -c "import keyring; keyring.set_password('agentvault:rczompsentinel','app_db','<pw>')"

# 2. Create/refresh the role + grants on BOTH databases:
psql "$OWNER_DSN_TOWER" -v app_pw='<pw>' -f mcplookup_app_role.sql
psql "$OWNER_DSN_FLY"   -v app_pw='<pw>' -f mcplookup_app_role.sql

# 3. Point the app at the least-priv role; keep the owner DSN for migrations:
flyctl secrets set \
  DATABASE_URL="postgres://mcplookup_app:<pw>@<app>-db.flycast:5432/zo_sentinel" \
  OWNER_DATABASE_URL="postgres://zo:<owner-pw>@<app>-db.flycast:5432/zo_sentinel"
```

## Verify least-privilege
```sql
-- as mcplookup_app: reads OK, identity writes OK, moat writes DENIED
SELECT count(*) FROM mcp_llm_axis_scores;                 -- ok
INSERT INTO users(...) VALUES (...);                      -- ok
INSERT INTO mcp_llm_axis_scores(...) VALUES (...);        -- ERROR: permission denied  (expected)
```

Re-runnable: the script is idempotent (creates the role if missing, refreshes grants otherwise).
