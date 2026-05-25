# Recursive Probe Loop — End-to-End Working (2026-04-26 21:23 UTC)

The loop closed in this session. Spec written on ZoComputer, no human typing on tower, result back in DB ~2 min later.

## End-to-end timeline (verified)

| Event | Time | Latency |
|---|---|---|
| Spec written: `/home/workspace/shared/work/probes/spec_e2e_health.json` | 21:20:55 | — |
| Syncthing mirrored to tower: `C:\Users\robin\ZoComputer\shared\work\probes\` | ~21:21:30 | ~35s |
| Tower's `ZoWarmWorker` Scheduled Task fired | 21:22:44 | ~70s after sync |
| `Invoke-Probe.ps1` ran, wrote result, exited | 21:22:45 | 0.69s |
| Result in `shared\outputs\probes\probe_*.json` | 21:22:45 | — |
| Syncthing mirroring back to ZoComputer | ~21:23:15 | (in progress) |
| `probe_consumer.py` (long-running) ingests | (next) | next 5s poll cycle |

**Total: ~2 minutes for the round trip with zero typing.**

## Components in production

```
/home/workspace/zo_mesh/probe_consumer.py                       v1.1, BOM-tolerant
/home/workspace/logs/_probe_consumer_oneshot.py                 manual single-pass invocation
/home/workspace/logs/_start_probe_consumer.py                   launches the daemon-wrapper version
/home/workspace/shared/work/probes/                             spec inbox (ZoComputer drops here)
/home/workspace/shared/outputs/probes/                          result inbox (consumer reads, then moves to processed/)

C:\Users\robin\ZoComputer\shared\code\tower\probes\Invoke-Probe.ps1            the dissolvable probe runner
C:\Users\robin\ZoComputer\shared\code\tower\probes\zo_warm_worker.ps1          the trigger consumer (60s)
C:\Users\robin\ZoComputer\shared\code\tower\probes\Install-ZoWarmWorker.ps1   installs the Scheduled Task
C:\Users\robin\ZoComputer\shared\work\probes\                                  tower spec inbox
C:\Users\robin\ZoComputer\state\zo_warm_worker.log                            tower-local log
```

## DB state (verified via zo_db_query)

| event_type | severity | rows |
|---|---|---|
| improvement_candidate | WARN | 2 |
| probe_result | INFO | 5 |
| probe_result | WARN | 2 |

First real `improvement_candidate` rows in the system. Both are:
```
kind: missing_security_headers
target: https://zo-sentinel-ui-robinc.zocomputer.io
summary: Missing 5 security headers ... | Also exposed: /openapi.json (HTTP 200)
```

## Bug fixes applied this session

1. **PowerShell BOM in JSON output.** Producer (`Invoke-Probe.ps1`) now uses `[IO.File]::WriteAllText` with BOM-less `UTF8Encoding`. Consumer (`probe_consumer.py`) now reads with `encoding='utf-8-sig'`. Defense in depth.
2. **Set-ExecutionPolicy.** Tower needed `RemoteSigned -Scope CurrentUser` for MCP-spawned shells to run local `.ps1` files. Done once, persists.
3. **CmdletBinding+param interaction.** Original `Invoke-Probe.ps1` hung when invoked from MCP-spawned shells. Rebuilt as flat-script form (no `[CmdletBinding()]`, no nested function dispatch via `param()` block defaults). Now runs reliably.

## What this enables next

- Schedule recurring probes (cron-style) on ZoComputer side. Drop a `dast_lite` spec every 4h, an `http_probe` every 5min, etc.
- Builder reads `improvement_candidate` rows from `mesh_events`, generates fix directives. Probe-then-verify cycle.
- Add new probe types: `tree_walk` (URL graph), `log_classify` (phi3 reads logs and buckets failures), `schema_diff` (OpenAPI changes).
- Each new probe is one bash function in `Invoke-Probe.ps1` plus optional handling in `probe_consumer.py`. No new transports needed.

## What's still needed for full hands-off

- `probe_consumer.py` running under `daemon_wrapper.sh` (not just one-shot via `_probe_consumer_oneshot.py`). One command: `python3 /home/workspace/logs/_start_probe_consumer.py`
- (Optional) ZoComputer-side scheduler that drops fresh spec files into `shared\work\probes\` on a cadence. Could be added to `watch_shared.py` as a periodic emitter, or as a cron-driven Python script in `/etc/zo` (subject to the externally-managed conf reset issue).

## Architecture summary

```
  HOT (ZoComputer/Modal)              WARM (Tower/Lenovo P520)
  ----------------------              ------------------------

  zo_write_file                        Scheduled Task ZoWarmWorker (1m)
      |                                       |
      v                                       v
  shared/work/probes/spec.json   --->   shared\work\probes\spec.json
      [Syncthing carries spec]                |
                                              v
                                         zo_warm_worker.ps1 (lockfile)
                                              |
                                              v
                                         Invoke-Probe.ps1 (dissolves)
                                              |
                                              v
  shared/outputs/probes/result.json  <---  shared\outputs\probes\result.json
      [Syncthing carries result]              |
      |                                       v
      v                                  spec moved to processed/
  probe_consumer.py (poll 5s)
      |
      v
  mesh_events: probe_result + improvement_candidate rows
      |
      v
  builder/sentinel/UI consume
```

Everything is dissolvable. Lockfiles prevent overlap. Specs and results are typed JSON. The only resident process on the tower is the Scheduled Task itself — fires for ~1s every minute, does work if there is any, exits.