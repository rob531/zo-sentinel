# Builder lane — what the autonomous builder builds (and what it doesn't)

**Standing decision, 2026-06-26.** After ~6 months in which ~142 autonomous build PRs
landed hollow (mock DBs, fake tables), the builder is re-scoped. The headline finding:
the ladder + directive + single-file builder is mismatched to *integrated, judgment-heavy*
work (app wiring, auth, UX). The durable value of the autonomous spend compounded on the
**data / model / infra** track — the SFT student model + 65,532-server scoring (the moat),
the 80k discovery registry, the Postgres schema + `app/` scaffold, `trust_gating_override`.

## Two lanes

**Builder lane (autonomous, directive-driven).** ONLY *similar, self-contained,
schema-bounded* modules with a machine-verifiable self-test:

- data enrichers, per-source adapters
- additional read-only API routers over the existing schema
- report / export endpoints
- scoring / ingestion jobs
- test suites

Three preconditions — a directive in this lane MUST carry all three, or it is not emitted:

1. **Real schema in context** — the directive names the real tables/columns (`mcp_llm_axis_scores`,
   `mcp_server_registry`, `Org`/`User`/`ApiKey`); the module imports `app.db` / `app.models`,
   never an inline/mock DB.
2. **A named, working exemplar** to mirror — currently `verdict_breakdown_api.py` (real, tested,
   applies `trust_gate`). The build clones its structure, data-access, and self-test pattern.
3. **A self-test gate** — a `__main__` self-test (TestClient + `dependency_overrides` → SQLite)
   that prints `PASS`/`FAIL`; the publisher only PRs a build whose `py_compile` **and** self-test pass.

Recipe: `goose_recipes/module_from_exemplar.yaml`. It also enforces a **lane guard**: if a task
is out-of-lane it emits `OUT_OF_LANE: <reason>` instead of a stub.

**Agent lane (in-session, NOT directive-driven).** Built by a capable integrated agent holding
the whole picture:

- the integrated app spine / `app/main.py` wiring
- authentication, session, JWT, OAuth, RBAC, tenancy/org-scoping
- all frontend / HTML / UX
- anything spanning multiple files or needing cross-module design judgment

## Why
A single-file builder can't carry integrated design judgment, and pointing it at that work
produced confident, compile-clean nothing. Cheap-model fan-out only pays when the unit of work
is genuinely fan-out-shaped **and** machine-verifiable. goose stays the orchestrator — pointed at
the builder lane, exemplar-gated, not open-ended "emit novelty."

See memory `builder-rescope-decision`, and `hollow_scaffold_root_cause_recipe_schema`
(root cause + the PR #791/#792 fix this generalizes).
