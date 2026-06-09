# ops/ — canonical source for host-only operational files

These files run on the **ZoComputer container**, not in this repo's normal import
path. They are version-controlled here for **history, review, and rebuild
recovery**, but the **live copies live on the container** and are deployed via the
`zo_write_file` MCP bridge (there is no git on the container for these paths).

> **Drift warning:** when you change one of these, update BOTH the repo copy here
> AND the live container copy (`zo_write_file`). They are not auto-synced.

## Live locations on the container

| Repo path | Live path on container |
|---|---|
| `ops/zo_mesh/watchdog.sh` | `/home/workspace/zo_mesh/watchdog.sh` |
| `ops/zo_mesh/*.sh` | `/home/workspace/zo_mesh/` |
| `ops/host/*.sh` | `/home/workspace/zo_sentinel/` |

## Inventory

### `ops/zo_mesh/` (mesh supervisor + zo_mesh-scoped ops)
- **`watchdog.sh`** (v3.7) — the mesh self-healer. `watchdog_daemon.py` runs it
  each ~6–9 min tick; it restarts dead daemons (incl. `goose_runner` with
  `env ZO_ESCALATE=1`, and `proposed_to_pending_promoter` as of v3.7). This file
  is the hardest to reconstruct — the main reason `ops/` exists.
- `archive_dead_dev_scripts.sh` — move retired zo_mesh dev one-offs to `archive/`.
- `prune_archived_graph_nodes.sh` — evict archived nodes from the code graph.
- `reseed_zo_mesh_gatetest.sh` — re-index zo_mesh + verify the `archive/` exclusion gates.

### `ops/host/` (zo_sentinel daemon ops)
- `deploy_phase45.sh` — refresh code, ensure `failure_matrix` view, restart goose_runner.
- `refresh_code.sh` — pull latest `main` onto the box (deploys merged fixes; publisher self-heals).
- `flip_zo_escalate.sh` — turn the Phase-5 escalation edge ON durably (`.zo_env` + relaunch).
- `verify_escalation_armed.sh` — confirm `ZO_ESCALATE=1` in the running goose_runner.
- `relaunch_ladder_keyed.sh` — rewire `ladder_shim` off the dead `key_hydrator` onto
  `/root/.zo_secrets` (gemini `RcGeminiAPIKey`) + secretless-ai (anthropic
  `PWD_ZO_COMPUTER_ANTHROPICAPI`). Pre-verify + fallback-to-bare.
- `restart_promoter.sh` — restart the `proposed_to_pending_promoter` daemon.
- `create_failure_matrix_view.sh` — create the `failure_matrix` view over `:8772`.
- `count_lock_conflicts.sh` — audit DuckDB write-lock conflicts vs the 651 baseline.
- `probe_secretless.sh` — READ-ONLY probe of the secretless-ai key path (names only).
