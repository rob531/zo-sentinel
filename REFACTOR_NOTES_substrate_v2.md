# Substrate Refactor v2 — uv / graphifyy / decoupled state loop-backs

Implements `project_specification.md` v2.0.0 (*Zo Sentinel Harness Optimization*):
resolve DuckDB multi-process write locks + container stability via `uv`,
`graphifyy`, and crash-resilient state loop-backs.

**Branch:** `feat/substrate-uv-graphify-loopback`

---

## TL;DR

The spec's central fix — decoupling DB reads/writes onto a single-writer bus —
**was already implemented and correct** in the runtime daemons. The new work is
the *isolation*, *indexing*, and *crash-resilience* layers (blueprints #1, #2,
#4). One literal instruction in blueprint #3 was **deliberately not followed**
because it would have regressed the architecture (details below).

| Blueprint | Status | What landed |
|---|---|---|
| #1 runtime_isolation_uv | **new** | `tools/uv_gate_runner.py` — PEP 723, Tier 0/1 gates in an ephemeral `uv run --isolated` interpreter |
| #2 contextual_graph_indexing | **new** | `tools/index_graph.py` + `.mcp.json`; `graphifyy[mcp]` installed as a uv tool; 24,394-node code graph built |
| #3 database_decoupling_strategy | **already done** | runtime daemons already route all I/O through `write_service:8772`; literal direct-`duckdb` read path **declined** |
| #4 stateful_loopback_contract | **new** | `state_loopback.py` (Default-FAIL `test-results.json` + `PROGRESS.md` + git checkpoint), `agents/evaluator.md` |

---

## Blueprint #3 — why the literal pattern was NOT applied

The spec's implementation pattern adds, into the data layer:

```python
import duckdb
db_reader = duckdb.connect("zo_mesh.db", read_only=True)   # blueprint #3 literal
```

This **conflicts with a hard architectural constraint** the repo already
enforces:

- `CLAUDE.md:250` — *"WriteService is the sole state bus. Never add a direct
  DuckDB import. All persistence flows through `ws_write` / `ws_query` /
  `ws_execute`."*
- `CLAUDE.md:32` — a *"direct DuckDB import"* is listed as a thing to **surface
  before doing**.
- The contract is actively tested: `tests/test_wiring.py` (see
  `gen_directives.py:221`) and `builder_test_hooks.py:142` fail the build on any
  `duckdb.connect(` in a runtime module; `queue_maturation.py:104` and
  `sentinel_director.py:288` instruct the LLM to *"NEVER duckdb.connect()"*.

The spec's **intent** (separate read-only analytics from ingestion so writers
never collide) is already met by the existing bus:

- **Reads** → `ws_query()` → `POST 8772/query`
  (`goose_runner.py:131`, `builder_mcp.py:159`).
- **Writes** → `ws_write()` → `POST 8772/write` with `wait:true`
  (`goose_runner.py:141`, `builder_mcp.py:89`) — one serialized single-writer.

Adding a second, direct read path would re-introduce the exact file-lock
contention the bus exists to eliminate, and break the wiring tests. So blueprint
#3's *goal* is satisfied; its *literal code* was correctly not applied.

Execution-instructions #2–#3 ("strip local sleep/timeout lock tricks") found
**nothing to strip in the three named files** — their `time.sleep` calls are
heartbeat/poll/boot-retry pacing, not lock backoff. The only place that still
keeps direct-DuckDB + lock-retry/backoff is `tests/gates/gate_framework.py`,
which talks to a **separate** `gate_errors.db` file shared across gate
processes. That is working test-infra with its own serialization; rewriting it
under this pass would be exactly the kind of risky rewrite the
`docs/INCIDENT_2026-05-09.md` postmortem warns against. **Recommended
follow-up:** give `gate_errors.db` its own single-writer endpoint and delete the
backoff, in a dedicated PR with the gate suite green before/after.

---

## Blueprint #1 — `uv` runtime isolation

`tools/uv_gate_runner.py` carries inline **PEP 723** metadata and is launched
with `uv run`, never a raw `python` against the shared container venv.

- **Tier 0 (syntax):** `ast.parse` + `py_compile`, in-process, no deps.
- **Tier 1 (import):** importlib smoke inside a fresh
  `uv run --isolated --python 3.11` interpreter, so a candidate's top-level side
  effects / missing deps can't mutate the parent process or hold a file handle.
  Falls back to a plain subprocess if `uv` is absent.

The gate is a **pure function of the file on disk** — no DB write, no DuckDB
connection — so evaluation stays read-only. Verified: both tiers PASS on
`state_loopback.py` via `uv run --isolated`.

---

## Blueprint #2 — `graphifyy` contextual indexing

- Installed isolated: `uv tool install "graphifyy[mcp]"` (v0.8.35) — verified a
  real, maintained PyPI package, not a typosquat.
- `tools/index_graph.py` runs the indexing lifecycle. Built **24,394 nodes /
  34,948 edges / 1,941 communities** over 1,935 code files (code-only,
  deterministic, no LLM, no network).
- **Semantic doc extraction (`--semantic`)** routes graphify's OpenAI-compatible
  backend at the **local ladder shim** (`127.0.0.1:8796/v1`), whose keys are
  AgentVault-hydrated inside `escalation.py` — the same loopback front door
  `goose_runner.py:663-666` uses. This indexes the 110 docs **without external
  HTTP** (the ladder is the one sanctioned outbound path). If the shim is
  unreachable it falls back to code-only and **refuses to reach an external
  LLM** — verified on this box (shim down → fallback fired).
- MCP server registered in `.mcp.json` with the spec's exact stdio command:
  `uv run --with "graphifyy[mcp]" python -m graphify.serve graphify-out/graph.json`.
- `graphify-out/` (21 MB graph + cache) is **git-ignored** — it is a
  regenerated artifact, not source.

---

## Blueprint #4 — crash-resilient state loop-back

`state_loopback.py`, built on **local files + git only** (no DuckDB, no
network), so loop progress survives a container collapse without ever taking a
DB lock:

- **`test-results.json` — Default-FAIL.** Every step starts `FAIL`; it flips to
  `PASS` only with explicit, non-empty verification proof (`record_pass` rejects
  a blank proof). A crash, a silent drop, or a never-run step all read `FAIL` —
  you can't mistake "didn't finish" for "passed". (Same anti-ghost-completion
  invariant `goose_runner.py` already enforces, lifted to a first-class
  manifest.)
- **`PROGRESS.md`** — human checkpoint log + a fenced machine-readable resume
  cursor; `resume()` parses it on spin-up.
- **`commit_checkpoint()`** — stages and commits *only* the two state files;
  no-ops safely off-git and when nothing changed; never raises.
- **`agents/evaluator.md`** — a read-only evaluator (Read/Grep/Glob + the
  read-only gate runner only; no write/db tools) that scores compile steps
  "without locking the disk layer".

Verified end-to-end: 6 blueprint steps seeded Default-FAIL, then flipped to PASS
with proof; `status` → `all_pass: true`.

---

## Validation

- `py_compile` clean on all new files **and** the three named target files
  (`builder_mcp.py`, `goose_runner.py`, `ladder_shim.py`) — execution
  instruction #4.
- `.mcp.json` parses as valid JSON.
- `uv_gate_runner.py` Tier 0 + Tier 1 (isolated) PASS.
- `graphify update .` produced a 21.5 MB graph; `--semantic` fallback guard
  fired correctly.

## Deploy

Additive change set on a feature branch. Per `CLAUDE.md`, deploy by merging to
`main` and pulling on the host; on first host run:
`uv tool install "graphifyy[mcp]"`, then `uv run tools/index_graph.py --semantic`
(the host has the ladder shim up).
