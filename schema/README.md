# `schema/` — the owner-split source of truth

This directory is the **canonical, version-controlled schema** for Zo Sentinel, split
by the boundary that matters: **the app vs. the builder.** It was extracted from
`full_schema_bootstrap.py` (which mixed both behind one single-writer) so the two
workloads are explicit and independently evolvable.

> **Why split.** Both halves currently live in one DuckDB behind the single writer on
> `127.0.0.1:8772`. That conflation is the root cause of the write-lock contention: the
> builder's append-heavy bursts (per-attempt `build_provenance`, graph seeding) compete
> with the app's transactional endpoint writes (`mcp_submissions`, `mcp_decisions`,
> `audit_log`, `auth_tokens`) on the *same* writer. Serializing through the bus *managed*
> the contention; separating the schemas is the first step to *removing* it.

## The two files

| File | Owner | Workload | Target engine |
|---|---|---|---|
| [`app.sql`](app.sql) | **zo_sentinel (the app)** — 5-endpoint API + user UI + admin UI | **OLTP** — concurrent transactional reads/writes, auth, atomic decision+audit | DuckDB today → **Postgres** (migration target) |
| [`builder.duckdb.sql`](builder.duckdb.sql) | **the builder** — the self-build feedback loop | **OLAP** — append-mostly, single-process, analytical aggregation | **DuckDB** (stays — correct engine here) |

**Rule of ownership:** a new table belongs in `app.sql` if an API endpoint or a UI reads
or writes it; it belongs in `builder.duckdb.sql` if only the build/eval loop touches it.
When in doubt, ask "does a *user* of the product ever see this row?" — yes ⇒ app.

## Status / scope of this split (Phase A)

- **Documentation + boundary only — zero runtime change.** The live bootstrap is still
  `full_schema_bootstrap.py`; these files are the reviewable source of truth that it
  *should* be generated from (follow-on, not done here). Re-running the bootstrap is
  still safe and idempotent.
- `app.sql` is the **current** DuckDB DDL, unchanged, with inline **PORTABILITY NOTES**
  marking each DuckDB-ism and its Postgres equivalent. The app schema turns out to be
  highly portable (native types throughout; the only deltas are `gen_random_uuid()`,
  sequences, and caller-side `INSERT OR IGNORE`).
- `builder.duckdb.sql` is explicitly **not** a migration target — DuckDB is the right
  engine for the analytical/graph/append workload. It is annotated "stays DuckDB."

## The transition this unlocks (not in scope here)

1. **Phase A (this):** split + owner-tag the schema. ✅
2. **Phase B:** make `write_service` a per-table **router** (app tables → a PG pool,
   builder tables → DuckDB) — still all DuckDB, just introduce the seam. The HTTP bus
   contract (`/write`, `/execute`, `/query`) stays identical, so the ~150 daemon call
   sites don't change.
3. **Phase C:** stand up Postgres, port the `app.sql` subset behind the router,
   shadow-read to validate, cut over. Builder tables never move.
4. **Phase D:** app endpoints get a real connection pool → the lock storm becomes
   *structurally impossible* for the app.

The `~19` modules that `import duckdb` directly (bypassing the bus) are the exceptions
that Phase C must port one by one — most are tests/tools, not endpoints.
