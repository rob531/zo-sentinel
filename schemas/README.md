# `schemas/` — committed point-in-time snapshots of the live databases

This directory holds machine-readable schema snapshots that probes and
evaluators consume as a source of truth. Without these, every probe
hardcodes column names from memory and breaks silently when the live
schema drifts.

## Files

| File | Engine | Source | Consumer |
|------|--------|--------|----------|
| `duckdb_schema.json` | DuckDB warehouse | `write_service@127.0.0.1:8772/information_schema` | Anything writing to / querying DuckDB |
| `mesh_memory_schema.json` | SQLite (`mesh_memory.db`) | `PRAGMA table_info` on `/home/workspace/Datasets/zo-mesh/mesh_memory.db` | `gh_actions_fetcher.py`, directive consumers, anything reading `mesh_memory` |
| `.pending_drift/` | (gitignored) | Written by `duckdb_schema_uptime_probe` when it detects live divergence | Robin-eyes-only staging area before promoting to canonical |

## How to consume

```python
from zo_sentinel.schemas.loader import load_mesh_memory_schema

schema = load_mesh_memory_schema()
cols = schema.column_names("mesh_memory")        # ['agent_id', 'memory_type', ...]
errs = schema.validate_row("mesh_memory", row)   # [] = valid
```

The loader is stdlib-only; safe to import from anywhere. See
[`../docs/SCHEMA_AS_CODE.md`](../docs/SCHEMA_AS_CODE.md) for the full
pattern.

## How to regenerate (tower-side only)

DuckDB access is loopback-bound (`127.0.0.1:8772`), so regeneration
only works on the tower itself.

```bash
ssh tower
cd /home/workspace/zo_sentinel
python3 refresh_schema_doc.py --dry-run   # preview without writes
python3 refresh_schema_doc.py             # writes DB_SCHEMA.md + 2 .json files
git diff schemas/                         # eyeball it
git add schemas/ DB_SCHEMA.md
git commit -m "chore(schema): refresh snapshots"
git push
```

The refresh script:
* Reads DuckDB via `write_service@127.0.0.1:8772/information_schema`
* Reads SQLite directly via `sqlite3` + `PRAGMA table_info`
* Embeds a `schema_hash` field (sha256 of sorted `(table, column, type)`
  tuples) so the uptime probe can detect drift cheaply
* `--dry-run` prints what it would write without touching disk

## Suggested cron / supervisord entries (NOT installed by this commit)

`refresh_schema_doc.py` is a one-shot, not a daemon. Robin can wire it
into cron when ready:

```cron
# /etc/cron.d/zo-sentinel-schema-refresh
# Refresh schema snapshots nightly, commit if changed.
17 3 * * * workspace cd /home/workspace/zo_sentinel && python3 refresh_schema_doc.py >> /home/workspace/logs/schema_refresh.log 2>&1
```

The drift-detection daemon (`zo_sentinel/probes/duckdb_schema_uptime_probe.py`)
ships dormant. A suggested supervisord block is in
[`docs/SCHEMA_AS_CODE.md`](../docs/SCHEMA_AS_CODE.md).

## Last regenerated

The `generated_at` field inside each JSON file is the ground truth.
The current seed snapshots are from **2026-05-25 22:38 UTC**,
back-translated from `DB_SCHEMA.md` because the live DuckDB is
loopback-only and isn't reachable from the host where this commit
was authored. The first tower-side `refresh_schema_doc.py` run will
overwrite both files with a freshly-introspected version; that diff
is expected.
