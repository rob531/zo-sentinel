# tower/

Operational scripts and infrastructure code that lives on the ZoComputer
tower at `/home/workspace/zo_mesh/` and `/home/workspace/`. Copied here so
the canonical version is source-controlled — the tower's `/home/workspace/`
is a local-only git checkout with no GitHub remote, so changes there don't
auto-sync to GH.

## Contents

| File | Tower path | Purpose |
|---|---|---|
| `go.sh` | `/home/workspace/zo_mesh/go.sh` | Full mesh recovery (`zm go`). Idempotent 18-section recovery script. |
| `apply_zm_go_fixes.sh` | `/home/workspace/apply_zm_go_fixes.sh` | One-shot runner for the 2026-05-28 zm go fixes. |
| `patch_go_sh_bootstrap_timeout.sh` | `/home/workspace/patch_go_sh_bootstrap_timeout.sh` | Bumps section 18 wait gate 60s → 120s. |
| `patch_goose_runner_double_log.sh` | `/home/workspace/patch_goose_runner_double_log.sh` | Drops redundant `print()` from `goose_runner.log()`. |
| `restart_threat_intel.sh` | `/home/workspace/restart_threat_intel.sh` | One-shot restart of threat_intel_ingestor after the `evidence(100)` fix. |
| `diagnose_oom.sh` | `/home/workspace/diagnose_oom.sh` | Capture OOM-killer evidence for the WorldAgent kill investigation. |

## 2026-05-28 patches included

1. **Tailscale userspace mode** (`go.sh` section 0.0) — Modal containers
   don't expose `/dev/net/tun`, so `tailscaled` must run with
   `--tun=userspace-networking` and route via SOCKS5/HTTP proxy on :1055.
2. **Bootstrap wait gate** (`go.sh` section 18, `full_schema_bootstrap.py`)
   — 60s → 120s to ride out write_service wrapper backoff cycles.
3. **goose_runner double-log fix** (`/home/workspace/zo_sentinel/goose_runner.py`)
   — see GH commit on `main`. Dropped redundant `print()` from `log()`.
4. **threat_intel DDL fix** (`/home/workspace/zo_sentinel/threat_intel_ingestor.py`)
   — see GH PR #8. Dropped MySQL prefix-length `evidence(100)`; added `source` field.

## Sync direction

GitHub (this repo) → tower: manual via the bundle/sync mechanism or by
running `apply_zm_go_fixes.sh` on the tower.

Tower → GitHub: manual via PR (this commit is the example).
