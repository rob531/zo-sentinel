# SOA "new atomic unit" — implementation record

Implements the design in `SOA_SERVICE_REGISTRY_DESIGN_2026-07-21.md` under the
CofC binding ruling of 2026-07-23 (folder-scan + **Option B** build-time
generation; human-gated first cohort; observe→enforce). Branch:
`feature/soa-atomic-unit`. Behaviour-preserving for prod; every step reversible.

## What the atomic unit becomes
A **service** — a self-contained directory `{logic.py, router.py, contract.py,
service.toml}` — not a loose file. The builder still writes **one file per
concern** (the proven `module_from_exemplar` lane); the *service* is the unit,
the *spine* does the mounting, and the builder never touches the spine.

## Steps landed

**Step 1 — authoritative fail-loud spine (`tools/generate_spine.py`).**
Promotes the report-only `spine_manifest.py` to the file prod runs. Reads
`services/active/*/service.toml`, emits `app/_spine_generated.py` (the fail-loud
`include_spine`). `app/main.py` now calls `include_spine(app)` instead of the old
hand-list + `except Exception: pass` loop — the invisibility bug the reachability
postmortem (FU-044) exists to kill. **CI** fails loud via `--strict` (broken
service) and `--check` (generated file drifted from `active/`); **prod** boots
anyway but records every outcome on `app.state` and surfaces it at
**`/spine/health`**. Each import_path is a literal, so the reachability census
still counts a live service as mounted — ratchet delta stays **0**.

**Step 2 — `services/active/` registry.** `tools/seed_active_registry.py` seeded
one `service.toml` per live router (31) from `_OPTIONAL_ROUTERS`. Presence in
`active/` **is** registration; no code was moved (reversible).

**Step 3 — spineful emission.** A canonical, *working* service-dir exemplar
(`services/_exemplar/`, contract passes) + a goose-native parent recipe
(`goose_recipes/service_dir_from_exemplar.yaml`, one bounded pass per file — the
subrecipe principle, canary-gated per GOOSE_WATCH) + a deterministic engine
bridge (`tools/service_decomposer.py`) that splits a service into single-file
directives. Intra-service imports are relative, so a dir survives staged→active
promotion with no rewrite.

**Step 4 — staged→active promotion gate (`tools/promote_staged_to_active.py`).**
The harness-engineering **correctness linter**: it runs each staged service's own
`contract.py` in a subprocess (real liveness — boots, mounts, serves 200,
schema-valid), checks route collisions vs active, and near-dups. A contract that
cannot even RUN is a **HOLD**, never a silent pass — the deliberate inverse of
FU-031's 74% Tier-0 degradation. Observe/report-only by default; `--enforce`
moves, capped (`--max-per-run`) for the human-gated first cohort.

## What fail-loud immediately surfaced (real finding, not silently changed)
Seeding revealed **6 of 31 hand-list entries were dead/duplicate no-ops** the
silent loop hid, plus **1 live router dead on a typo**:
- 4 expose **no router** (`entity_report_exporter`, `org_api_key_manager`,
  `overview_dashboard_api`, `verdict_watchlist_service`) → mount nothing.
- `dashboard_summary_api`'s only route is **shadowed** by `verdict_breakdown_api`.
- `server_axis_scores_summary_router` imports `MCPServerRegistry` (should be
  `McpServerRegistry`) → **fails to import**, dead on `main` today. A one-char
  fix likely revives a live-listed router.

All are recorded in `tools/spine_known_issues.json` (with reasons) so the gates
are **satisfiable** on the seed but fail on any *new* dead/duplicate/broken
service — the reachability-ratchet discipline applied to services. They are the
**Step-6 triage backlog**, to be fixed/redirected/deleted *deliberately*, not
here.

## Gates (all green in the worktree)
`reachability_ratchet --enforce` (delta 0) · `pull_check` (capmap, drift 0) ·
`generate_spine --check --strict` · ruff F,E9 (blocking surface) · new
`tests/test_spine.py` + `tests/test_promote_staged_to_active.py` (10 tests) ·
new smoke-ladder `tier4_spine` (runtime failures within allowlist).

## Not done here (deliberately)
- **Arming** the subrecipe recipe / builder emission into the live loop — needs a
  canary + **FU-031** fixed first (else `contract.py` liveness degrades and the
  gate measures nothing). Recipe + decomposer are staged, not wired to `go.sh`.
- **Step 6** triage of the 6 known issues + the 317-orphan graveyard.
- Prod deploy — Fly deploy is manual; merge ≠ deploy.

## Forward work (chairman note, 2026-07-24)
> "We need an orphanage and an understanding / history of how orphans got there."

The 317 orphans need a **home + provenance**: not just a count, but *per-orphan*
origin (which ladder run / directive / PR emitted it, why it never mounted —
edit-class `output_file:null`, hollow, superseded, comment-only match). The
census already carries each orphan's shape; the missing axis is **history**.
Candidate: an `orphanage/` manifest joining `reachability_ratchet.json` orphans
to `mesh_events`/git-blame origin, so triage (Step 6) decides from *why it exists*
rather than *that it exists*. This is the natural companion to the fail-loud spine
— the spine stops NEW orphans; the orphanage explains and retires the OLD ones.
