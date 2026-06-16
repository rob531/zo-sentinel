# Scope: App data DuckDB → PostgreSQL (builder stays on DuckDB)

*Status: SCOPE (design only — not yet in execution). Council 3+1, 2026-06-16.*

## Decision

- **Move the APP / product data to PostgreSQL; keep the BUILDER / factory state on DuckDB.**
- **Justified:** the single-writer DuckDB + `write_service:8772` model is the documented, recurring
  root of lock-storm / OOM / write-service-SPOF outages. The product data is written *concurrently*
  by many signal daemons and served live by the FastAPI APIs (~1M `mcp_signal_scores` rows) — the
  multi-writer OLTP shape DuckDB is wrong for. Postgres (MVCC, pooling, concurrency) removes that root.
- **Scope now, EXECUTE LATER.** Do NOT begin migrating until **(1)** the LLM-risk convergence cutover
  has landed, and **(2)** auto-merge has bedded in over a clean window. One axis at a time.

## What moves vs. stays

| Stays on DuckDB (builder/factory, via `write_service`) | Moves to Postgres (app/product) |
|---|---|
| `mesh_memory` (directives, build_artifact, verd%, watermarks) | `mcp_server_registry` (~1690) |
| `build_provenance` | `mcp_signal_scores` (~1,000,000) |
| `mesh_events`, `agent_runs`, `inference_log`, `service_health`, watchdog/janitor state | `mcp_signal_enrichments` |
| (low-volume, single-writer-friendly, internal) | `mcp_risk_register`, `mcp_attestations`, `mcp_definition_history`, `mcp_threat_associations`, verdicts, `mcp_llm_axis_scores` (the LLM cutover home) |

## Cutover coordination (important / time-sensitive)

The pending **LLM-risk cutover** writes `mcp_llm_axis_scores` + `registry.risk_tier`. It must **NOT** be
coupled to this migration (it's mid-flight, scores frozen). Plan:
- Land the cutover on the **current DuckDB** when scores are ready (unblocked), BUT with a
  **Postgres-portable** `mcp_llm_axis_scores` schema so the later migration lifts it mechanically.

### Postgres-portable `mcp_llm_axis_scores` DDL (use for the DuckDB cutover too)
```
server_id        VARCHAR    NOT NULL
axis_name        VARCHAR    NOT NULL
label            VARCHAR    NOT NULL          -- model argmax (auditable)
label_index      INTEGER    NOT NULL          -- risk_axis_mapping_v1 index; -1 = unmapped
probs            JSON                          -- DuckDB JSON == Postgres JSONB (portable)
p_top            DOUBLE PRECISION
p_critical       DOUBLE PRECISION              -- overall_risk; else NULL
p_danger         DOUBLE PRECISION              -- minority axes; else NULL
escalated        BOOLEAN    NOT NULL DEFAULT FALSE
escalated_to     VARCHAR                       -- 'CRITICAL' | 'REVIEW' | NULL
decision_rule_version VARCHAR
model_version    VARCHAR
adapter_sha256   VARCHAR
scored_at        TIMESTAMP  DEFAULT now()
PRIMARY KEY (server_id, axis_name, model_version)
```
Portability rules: explicit types only; no DuckDB-only idioms; **no implicit sequences** (composite
PK, no AUTOINCREMENT); JSON not DuckDB STRUCT; `ON CONFLICT` upserts (works in both); FK-clean.

## Staged migration plan (per-table; NO big-bang)

0. Stand up Postgres alongside the stack; add a Postgres-backed `write_service` mode (or a thin
   data-access layer) so the API surface is unchanged for callers.
1. Introduce a **DB-access abstraction**: app code targets Postgres; builder code keeps
   `write_service`/DuckDB. Two clean lanes.
2. **Per table, in order** (start smallest/lowest-risk → highest): `mcp_attestations` /
   `mcp_definition_history` → `mcp_risk_register` → `mcp_signal_enrichments` → `mcp_server_registry` →
   `mcp_signal_scores` (~1M, last):
   a. dual-write (DuckDB + Postgres) → b. backfill + row-count/҂checksum verify → c. cut reads to
   Postgres → d. observe → e. stop the DuckDB write → f. decommission the DuckDB table.
3. Repoint the FastAPI product APIs table-by-table as reads cut over.

## Architect DB-contract update (DRAFT — do NOT activate until execution)

The architect's hardcoded context currently says *"DB access ONLY via `write_service:8772`; never
`import duckdb`; use `ON CONFLICT` not `INSERT OR IGNORE`."* For generated **app** code this must become:
*"App/product data → Postgres via `<app_db_client>` (pooled); builder/factory data → `write_service`
(DuckDB). Never `import duckdb` or raw `psycopg` in generated modules — use the data-access layer."*
**Prerequisite:** flip this contract BEFORE generating any Postgres-targeting app code, or the architect
keeps emitting DuckDB code and fights the migration.

## Guards (binding)

- **Migration PRs are EXEMPT from auto-merge** — manual, ordered, human-gated (a backfill/cutover PR
  must never auto-land). (My `auto-merge.yml` only fires on the `autonomous-build` label, so
  human-authored migration PRs are already excluded; do NOT label them `autonomous-build`.)
- **Honor the convergence freeze** — zero edits to frozen scoring modules during the cutover window.
- **One table at a time**; never move builder state off DuckDB.
- Each table has a **rollback**: keep dual-write until reads are verified clean; revert = cut reads back
  to DuckDB (still being written) and drop the Postgres reads.

## Execution preconditions (gate)

Do NOT start step 1 until ALL hold:
1. LLM-risk cutover landed (on DuckDB, portable schema). 2. Auto-merge bedded in (clean week, triage
   solid-ratio healthy). 3. A concrete concurrency/scale pain on the product is demonstrable (or the
   single-writer is confirmed the active bottleneck). 4. The architect DB-contract change is ready.

## Open questions for the owner

- Postgres host: managed (RDS/Supabase/Neon) vs self-hosted on the tower? (affects daemon reachability)
- Does the box's network reach the chosen Postgres? (the daemons run on ZoComputer)
- Connection-pooling layer (pgbouncer?) given many daemons.
