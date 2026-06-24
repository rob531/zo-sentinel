# Zo-Sentinel app-data Postgres (tower now, Fly later)

The app-data store (SaaS tables `orgs/users/api_keys` + the threat-intel `mcp_server_registry` /
`mcp_llm_axis_scores`) lives in **Postgres**. The builder/factory keeps its **DuckDB** unchanged;
only the *app data* lives here. The schema is the Alembic migrations in `../../migrations`
(`0001_initial` = SaaS spine, `0002_registry_scores` = registry + SFT scores) and is **identical
across environments** -- only the `DATABASE_URL` changes. That is the whole point: stand it up on
the tower now, repoint at Fly later, zero schema rework.

## Tower (Windows, no Docker) -- now
```powershell
$env:PGPASSWORD     = "<postgres superuser pw chosen at install>"
$env:ZO_PG_PASSWORD = "<pw for the app role 'zo'>"
./infra/postgres/setup_tower_postgres.ps1     # winget-installs PG16, creates the DB, runs migrations
```

## Linux / dev (Docker)
```bash
cp infra/postgres/.env.example infra/postgres/.env   # edit the password
docker compose -f infra/postgres/docker-compose.yml --env-file infra/postgres/.env up -d
DATABASE_URL=postgresql://zo:<pw>@localhost:5432/zo_sentinel alembic upgrade head
```

## Fly.io (mod later)
Provision Fly Managed Postgres, then **drop the local DB step entirely** and just:
```bash
DATABASE_URL="<fly managed postgres DSN>" alembic upgrade head
```
The app already reads `DATABASE_URL` (`app/db.py` normalizes `postgres://` -> psycopg). Same container,
same migrations; Fly is a DSN change.

## Why
Moving registry + scores off the single-writer DuckDB removes the `:8772` write-congestion that
forces the slow, staggered 20K ingest -- Postgres (MVCC) takes concurrent writes natively. See
`docs/POSTGRES_APP_MIGRATION_SCOPE.md` and `20k_staged_rollout_plan.md`.