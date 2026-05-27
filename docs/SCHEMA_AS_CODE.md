# Schemas as code

## Why

zo-sentinel's data layer spans two engines:

* **DuckDB** warehouse, behind `write_service@127.0.0.1:8772` (loopback only).
* **SQLite** `mesh_memory.db` (~5 cols), accessed via direct `sqlite3`.

Until this PR the only schema documentation was `DB_SCHEMA.md` -- a
human-readable Markdown dump produced by `refresh_schema_doc.py`. Probes
and evaluators hardcoded column names from memory, and when the live
schema drifted they failed silently (or worse, the WriteService rejected
every write with `400 Bad Request` while idempotent retries pretended
nothing was wrong). `builder_conventions.json` tries to lecture authors
into checking the schema, but lecture isn't a mechanism.

This pattern replaces the lecture with a code path:

1. **Committed JSON snapshots** at `schemas/`, regenerated tower-side
   by `refresh_schema_doc.py`, are the single source of truth.
2. **`zo_sentinel.schemas.loader`** (stdlib-only) reads them and exposes
   `column_names()` / `validate_row()` to probes.
3. **`zo_sentinel.probes.duckdb_schema_uptime_probe`** continuously
   compares live schema vs. committed snapshot, stages divergence to
   `schemas/.pending_drift/` for human review, and writes a high-
   importance row to `mesh_memory` so the Directive Architect can act on it.

## Refresh workflow

DuckDB is loopback-only, so regeneration only works on the tower:

```bash
ssh tower
cd /home/workspace/zo_sentinel
python3 refresh_schema_doc.py --dry-run   # preview, no writes
python3 refresh_schema_doc.py             # writes DB_SCHEMA.md + 2 .json
git diff schemas/                          # sanity-check
git add schemas/ DB_SCHEMA.md
git commit -m "chore(schema): refresh snapshots"
git push
```

## How a probe consumes the loader

```python
from zo_sentinel.schemas.loader import load_mesh_memory_schema

schema = load_mesh_memory_schema()
cols = schema.column_names("mesh_memory")  # ['agent_id', 'memory_type', ...]
row = build_my_row(...)
errors = schema.validate_row("mesh_memory", row)
if errors:
    log.error("schema validation failed: %s", errors)
    return  # fail loud rather than POST a 400-guaranteed row
```

Three lines. The loader is stdlib-only so it imports anywhere.

## What to do when the snapshot is stale

A snapshot is "stale" if its `generated_at` timestamp is older than
`STALE_AFTER_DAYS = 14` days. By default `load_*_schema()` returns the
snapshot regardless of age (defensive: better to validate against a
slightly-old schema than against nothing). Probes that need strict
freshness can opt in:

```python
from zo_sentinel.schemas.loader import load_duckdb_schema, StaleSchemaError

try:
    schema = load_duckdb_schema(ensure_fresh=True)
except StaleSchemaError as e:
    log.error("snapshot too old, aborting: %s", e)
    sys.exit(2)
```

Snapshot missing entirely -> `FileNotFoundError`. Snapshot corrupt ->
`ValueError`. Both are fail-loud; the recurring failure mode this
project exists to prevent is *silent* drift, so missing/stale data must
not pretend to succeed.

## Drift-detection probe operation

`duckdb_schema_uptime_probe` ships **dormant** -- no supervisord block,
no cron entry. Robin enables it manually when ready.

Manual run (one cycle, exits):

```bash
cd /home/workspace/zo_sentinel
python3 -m zo_sentinel.probes.duckdb_schema_uptime_probe --once
# add --dry-run to skip mesh_memory writes
```

Daemon mode:

```bash
nohup python3 -m zo_sentinel.probes.duckdb_schema_uptime_probe \
    --interval 300 >> /home/workspace/logs/duckdb_schema_uptime_probe.log 2>&1 &
```

Suggested supervisord block (paste into `supervisord.conf` when ready):

```ini
[program:duckdb_schema_uptime_probe]
command=/usr/bin/python3 -m zo_sentinel.probes.duckdb_schema_uptime_probe --interval 300
directory=/home/workspace/zo_sentinel
autostart=true
autorestart=true
stdout_logfile=/home/workspace/logs/duckdb_schema_uptime_probe.log
stderr_logfile=/home/workspace/logs/duckdb_schema_uptime_probe.log
stdout_logfile_maxbytes=10MB
stdout_logfile_backups=3
```

Behaviour:

* **Uptime monitoring.** `SELECT 1` against `/query` every cycle. Emits
  to `mesh_memory` only on state transitions (`ok->down`, `down->ok`) and
  every Nth no-change cycle as a liveness pulse (`N=12`, i.e. ~1/hour at
  the default 5-minute cadence). This is deliberate: heart-beating every
  cycle would flood `mesh_memory` and drown the genuinely-interesting
  outage signal.
* **Drift detection.** Every successful uptime cycle: re-query
  `information_schema`, compute the canonical schema hash, compare to
  the hash baked into `schemas/duckdb_schema.json`. Mismatch:
  * Write the new schema to `schemas/.pending_drift/duckdb_schema.json`
    (gitignored). NEVER overwrites the canonical file.
  * Emit `memory_type="duckdb_schema_drift"`, `importance=0.8` with
    `{added_tables, removed_tables, modified_columns, pending_path}`.
  * Robin reviews the staged file and `cp .pending_drift/duckdb_schema.json schemas/`
    + commit to promote.
* Same cycle: introspect `mesh_memory.db` via `PRAGMA table_info`,
  same compare + stage flow for `mesh_memory_schema.json`.

**Why staged-not-committed drift:** auto-committing schema changes from
a daemon risks committing transient state (a half-applied migration, a
brief WS reboot exposing a tables-missing window). Staging keeps the
probe purely observational; promotion stays human.

## What's intentionally deferred

* **`gh_actions_fetcher.py` adoption** of the loader -- that file is on
  the unmerged `feature/gh-actions-evaluator` branch (PR #2). Lands as
  a follow-up PR after PR #2 merges.
* **Other 30+ probe/evaluator scripts** -- adopt opportunistically when
  touched, not as a big-bang sweep.
* **CI gate** that fails PRs referencing columns not in the snapshot --
  defer; first prove the pattern.
* **Tower-side auto-refresh daemon** -- document, don't ship.
