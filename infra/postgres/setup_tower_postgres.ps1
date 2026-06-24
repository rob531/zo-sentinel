<#
  Stand up Postgres on the TOWER (Windows -- no Docker) for Zo-Sentinel app data.
  Installs PostgreSQL via winget, creates the role + database, then applies the schema
  with `alembic upgrade head`. Idempotent: safe to re-run.

  MOD-LATER (Fly.io / Linux): you do NOT run this script. Instead set DATABASE_URL to the
  Fly Managed Postgres DSN and run `alembic upgrade head` from the repo root -- the schema is
  identical (these same migrations). docker-compose.yml is the Linux/dev equivalent of this script.

  Usage:
    $env:PGPASSWORD   = "<the postgres superuser password chosen at install>"
    $env:ZO_PG_PASSWORD = "<password for the app role 'zo'>"
    ./infra/postgres/setup_tower_postgres.ps1
#>
param(
  [string]$PgVersion  = "16",
  [string]$DbName     = "zo_sentinel",
  [string]$DbUser     = "zo",
  [string]$DbPassword = $(if ($env:ZO_PG_PASSWORD) { $env:ZO_PG_PASSWORD } else { "change-me" }),
  [int]$Port          = 5432
)
$ErrorActionPreference = "Stop"

Write-Host "[1/4] ensure PostgreSQL $PgVersion is installed (winget)"
if (-not (Get-Service "postgresql*" -ErrorAction SilentlyContinue)) {
  winget install --id "PostgreSQL.PostgreSQL.$PgVersion" --silent --accept-package-agreements --accept-source-agreements
} else {
  Write-Host "  a postgresql service already exists -- skipping install"
}

$psqlExe = (Get-ChildItem "C:\Program Files\PostgreSQL\$PgVersion\bin\psql.exe" -ErrorAction SilentlyContinue | Select-Object -First 1)
if (-not $psqlExe) { throw "psql not found under C:\Program Files\PostgreSQL\$PgVersion\bin -- check the install / PgVersion" }
$psql = $psqlExe.FullName

Write-Host "[2/4] create app role + database (idempotent; needs `$env:PGPASSWORD = postgres superuser pw)"
& $psql -U postgres -h localhost -p $Port -v ON_ERROR_STOP=1 -c @"
DO `$do`$ BEGIN
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname='$DbUser') THEN
    CREATE ROLE $DbUser LOGIN PASSWORD '$DbPassword';
  END IF;
END `$do`$;
"@
$exists = (& $psql -U postgres -h localhost -p $Port -tAc "SELECT 1 FROM pg_database WHERE datname='$DbName'")
if (-not $exists) { & $psql -U postgres -h localhost -p $Port -c "CREATE DATABASE $DbName OWNER $DbUser" }

Write-Host "[3/4] apply schema (alembic upgrade head)"
$env:DATABASE_URL = "postgresql://${DbUser}:${DbPassword}@localhost:${Port}/${DbName}"
python -m alembic upgrade head

Write-Host "[4/4] done. App data Postgres is up. DATABASE_URL set to the zo_sentinel DB."
Write-Host "      Verify: psql -U $DbUser -d $DbName -c '\dt'"