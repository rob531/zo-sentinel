# Design: graph-native feedback loop (goose + DuckDB + graphify)

Status: **Phases 1–5 implemented + deployed.** Phase 6 wiring guard added. Phase 1
shipped first; Phases 2–3 (graph→DuckDB + architect graph tools) deployed on the
box; Phases 4–5 (failure-pattern matrix + escalation) landed 2026-06-09 (matrix
LIVE, escalation behind `ZO_ESCALATE`, default OFF).
Decision source: 2026-06-07 ultracode 17-agent evaluation + adversarial verification.
Related memory: substrate refactor (uv/graphifyy/state_loopback), `go.sh` lock-hardening + v2.9.2 wave startup, DuckDB lock root-cause.

## Goal

Give the build sessions richer, grounded context and make the **directive generator + architect** self-evaluating — *without* re-introducing DuckDB write-lock contention or the ghost-completion regressions. Serves the standing goal: substrate stability + decoupled state, on the `127.0.0.1:8772` write_service bus, **no direct `duckdb` import** (CLAUDE.md:250).

## Decisions

| Question | Answer | Why |
|---|---|---|
| Is the graphifyy **package** useful, or replace with DuckDB? | **Hybrid** | Keep graphifyy as the build-time tree-sitter AST extractor (28-lang call/import/inherit edges + baked Leiden communities — not reproducible in DuckDB). Drop the *live* graphify MCP at runtime; serve the architect from a **DuckDB copy** of `graph.json` over the bus so the code graph can be JOINed to live pass/fail tables. |
| DuckDB-native equivalent feasible? | **Mostly** | node-link JSON maps 1:1 to `code_nodes`/`code_edges`; neighbors/path/community/blast-radius via `SELECT` + bounded recursive CTE (`list_contains` cycle guard). Cannot reproduce: AST extraction, fresh community detection — so extraction stays on graphifyy. |
| Adopt `lucasrosati/claude-code-memory-setup`? | **NO** | Its only technical component is graphify (older than ours), wrapped in an Obsidian vault + chat-extractor + cron/post-commit hooks that conflict with the `:8772`/DuckDB single-source-of-truth and the `state_loopback` Default-FAIL model, add a data-sensitivity surface, and don't address the actual gap. Session memory is already DuckDB `mesh_memory`'s job. |

## Architecture — three feedback edges

1. **Structural-context edge** (DuckDB copy, not the live MCP). An ~80-line loader reads `graphify-out/graph.json` and batch-POSTs to `:8772/write` into `code_nodes{id,label,norm_label,file_type,source_file,source_location,community,built_at_commit}` and `code_edges{src,dst,relation,weight,confidence_score,context,source_file,source_location,built_at_commit}` (~2k rows/POST; every row stamped with git HEAD so rebuild = append + `WHERE built_at_commit=MAX`). The architect reads it via new `graph_neighbors`/`graph_path` `@mcp.tool`s in `builder_mcp.py` (already loaded as `zo_builder_bridge` — no new extension) **and** a pre-computed context blob folded into `{{task_description}}`. The decisive advantage a standalone MCP can't give: JOIN the graph to `agent_runs`/`inference_log`/`corrections`/`mesh_memory`.
2. **Eval→orchestrator edge** (Phase 1, shipped). `state_loopback` + `uv_gate_runner` wired into the loop: Default-FAIL manifest, Tier-0 syntax gate on completion, FAIL history, resume cursor.
3. **Escalation edge** (Phase 5, highest risk). Lift the `GOOSE_MODEL` pin + drop the `high→coworker` short-circuit so failed directives re-assert up the ladder — **one alias per attempt** (preserve #73; no mid-build rung switching).

## Phased plan

| Phase | What | Touches | Status |
|---|---|---|---|
| **1** | Eval edge: `state_loopback`+`uv_gate_runner` in the loop; Tier-0 gate replaces output-only check; FAIL history + resume | `goose_runner.py`, `.gitignore` | **DONE** |
| **2** | Persist graph → DuckDB (multi-repo, bulk `read_json` load, `built_at_commit`) | `tools/load_graph_to_bus.py` | **DONE** (deployed) |
| **3** | Structural-context edge: `graph_neighbors`/`graph_path` tools + architect step-0 + pre-blob | `builder_mcp.py`, `architect.yaml`, `goose_runner.py` | **DONE** (deployed) |
| **4a** | **Instrumentation:** `build_provenance` row per build ATTEMPT (the matrix substrate, previously defined-but-unwired) | `build_routing.py`, `goose_runner.py` | **DONE** |
| **4b** | **Failure-pattern matrix:** `failure_matrix` view + `build_success_stats` MCP tool | `full_schema_bootstrap.py`, `builder_mcp.py`, `architect.yaml` | **DONE** |
| **5** | **Escalation edge** (⚠ behind `ZO_ESCALATE`, default OFF): a FAILED directive re-asserts UP the ladder, one alias per attempt | `build_routing.py`, `goose_runner.py` | **DONE** (flagged off) |
| **6** | Wiring guard test (no `import duckdb`; escalation off=pinned/on=bumped+free-capped; provenance id idempotent) | `tools/test_graph_native_wiring.py` | **DONE** |
| — | Keep graph fresh post-PASS (auto `index_graph.py update` + reload in the loop) — mechanism exists as host scripts; not yet auto-triggered | `goose_runner.py` | follow-on |
| — | **Matrix-driven** rung selection (read `failure_matrix` to pick the best rung per class, replacing the static bump) — needs `build_provenance` volume | `build_routing.py` | follow-on |

## Regression caveats (from May-2026 session memory) — MUST hold

- **The host refresh does `git reset --hard origin/main` on `/home/workspace/zo_sentinel`** (only `zo_sentinel`, not `zo_mesh`). Therefore **runtime state files must be untracked** — `test-results.json` and `PROGRESS.md` are now gitignored so they survive `reset --hard` (untracked files are left alone) and Modal reboot (disk persists). *They were tracked in the substrate PR; this PR untracks them — that was a latent regression.*
- **`git reset --hard` wipes uncommitted edits**, so `commit_checkpoint` on the host clone is futile/conflicting → it is **OFF by default**, gated behind `ZO_STATE_GIT_COMMIT=1` (off-host only). The on-disk manifest is the resume source.
- **"`zo_db_query` destabilizes write_service"** under load → Phase 1 is **file-based, zero DB load**. Phases 2–3 add graph rows/queries: batch writes (~2k/POST), bounded recursive CTEs, cache per cycle.
- **`write_service` is a single DuckDB connection** (thread-safety crash, code 134, PR #35) — never add a second writer; everything stays serialized through `:8772`.
- **Tier-1 import gate false-FAILs on host-only deps** (mcp/httpx/abs paths) → completion gates on **Tier-0 syntax only**; Tier-1 is advisory (recorded, not blocking) until calibrated.
- **Escalation (#73): one coherent model alias per attempt** — switch only *between* attempts, never mid-build.

## Idempotence (design invariant)

- `state_loopback.init_manifest` uses `setdefault` (re-entry preserves prior PASS/FAIL); `record_pass`/`record_fail` overwrite; `commit_checkpoint` no-ops when nothing changed.
- `_complete` is reached only past the `.done` sentinel guard (`is_goose_eligible`), so a completed directive never re-processes.
- The graph loader (Phase 2) is append-keyed by `built_at_commit` and idempotent per commit.
- Per the established ops pattern, any host patcher is all-or-nothing, self-tests compose + a second pass, and refuses partial application.

## Phase 1 — what shipped here

`goose_runner.py`:
- imports `state_loopback as sl` + `tools.uv_gate_runner.run_gates` (guarded; `None` → gate no-ops, no regression).
- `run()` startup: `sl.resume()` logs the last checkpoint.
- per directive: `sl.init_manifest([directive_id])` (Default-FAIL).
- completion gate (both goose + fallback paths): `output_confirmed(...) AND _syntax_gate(...)` — a file that lands but doesn't parse no longer stamps `.done`.
- `_complete`: `record_pass` + `checkpoint` (+ optional commit); `_ghost_or_fail`: `record_fail` with attempt count.
- `.gitignore`: `test-results.json`, `PROGRESS.md` untracked.

Verified: `py_compile` clean; isolated sim shows a broken build held at FAIL while a good build passes, idempotent across re-init.

## Phase 4–5 — what shipped (2026-06-09)

**Linchpin:** the escalation edge couldn't learn because the per-attempt
rung+outcome data was never captured — `build_provenance` was defined-but-unwired
(`full_schema_bootstrap.py`), `inference_log` only covers enrichment, and the
build path pinned HIGH/CRITICAL to rung-0. So the real order was **instrument →
aggregate → escalate**.

- **4a instrumentation** — `build_routing.build_provenance_row()` (pure helper,
  deterministic `build_id = directive:outcome:attempt:day` → idempotent under
  `INSERT OR IGNORE`); `goose_runner._record_build_provenance()` (best-effort
  `ws_write`, never fails the build) called from `_complete` (success/`pass`) and
  `_ghost_or_fail` (per-attempt `ghost`). Every attempt now records
  complexity/model/smoke_result/rescue_count/success/error.
- **4b matrix** — `failure_matrix` view aggregates `build_provenance` by
  (directive_type, complexity, model) → success%, avg_rescues, last_error.
  `build_success_stats` MCP tool exposes it to the architect (read-only,
  `:8772/query`); listed in `architect.yaml`.
- **5 escalation** — `build_env_for(directive, attempt=)` bumps `GOOSE_MODEL` up
  `_ESCALATION_LADDER` (low→medium→high→critical) by the prior ghost count,
  **capped at the top free rung** unless complexity=critical (the paid rung 15 is
  governed by escalation.py's cost gate regardless). `goose_runner` passes
  `ghost_attempts()`. **Behind `ZO_ESCALATE`: unset → attempt ignored, pinned env
  exactly as before** (zero regression). Each retry is a fresh goose subprocess
  pinned to one alias, so #73 (no mid-build switching) holds for free.

Verified: `tools/test_graph_native_wiring.py` (6 tests) — no `duckdb` import;
flag-off ignores attempt; flag-on bumps + free-caps non-critical; critical reaches
the paid rung; provenance id idempotent and collision-free.

**Activation:** set `ZO_ESCALATE=1` in the goose_runner env on the box once
`build_provenance` has accrued enough rows to trust the climb. Then the follow-on
(matrix-driven rung selection) replaces the static bump.
