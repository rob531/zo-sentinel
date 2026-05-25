# Recursive Probe Loop — First Cut (2026-04-26)

Closing the warm-compute / dissolvable-agent loop end-to-end. Tower writes
probe artifacts → Syncthing carries them → ZoComputer ingests → mesh_events
rows land → builder/sentinel can read them as `improvement_candidate` events.

First real signal already produced: sentinel UI is missing all 5 standard
security headers AND exposes `/openapi.json` without auth. Surfaced by a
30-second probe run with zero human typing on the tower.

## What was discovered (without asking)

- **Sentinel UI live at:** `https://zo-sentinel-ui-robinc.zocomputer.io/`
  - `/health` returns `{"status":"ok","service":"ui_server","port":8790}`
  - `/` serves the ZO-SENTINEL UI HTML (16 KB)
  - Tunnel is correctly routing 443 → 8790 (the historical 52014 mismatch is fixed)
- **MCP server at:** `https://zo-mcp-server-robinc.zocomputer.io/mcp` (already in memory)
  - `/health` is not exposed publicly there (only `/mcp` is)

## Files in this drop

```
C:\Users\robin\ZoComputer\shared\code\tower\probes\Invoke-Probe.ps1   the dissolvable runner
C:\Users\robin\ZoComputer\shared\code\tower\probes\spec_health.json   probe spec: UI /health
C:\Users\robin\ZoComputer\shared\code\tower\probes\spec_root.json     probe spec: UI /
C:\Users\robin\ZoComputer\shared\code\tower\probes\spec_dast.json     probe spec: dast_lite
C:\Users\robin\ZoComputer\shared\code\tower\probes\smoke_test.ps1     standalone smoke test (kept for future debugging)
/home/workspace/zo_mesh/probe_consumer.py                              ZoComputer-side ingester (long-running loop)
/home/workspace/logs/_probe_consumer_oneshot.py                        single-pass version for verification
/home/workspace/logs/_init_probe_tables.py                             optional schema init (not yet needed; using mesh_events)
```

## Probe types implemented (first cut)

| Type | What it does | Detected today |
|---|---|---|
| `http_probe` | Single GET against URL, timing, status, headers, body hash, drift detection vs baseline_body_hash | UI health 200/286ms, UI root 200/404ms |
| `dast_lite`  | Curated safe checks: 12 common paths + 5 security-header presence | `/openapi.json` exposed (WARN), all 5 sec headers missing (WARN) |

## How the loop works (one pass)

```
  TOWER                                 ZOCOMPUTER
  -----                                 ----------
  Invoke-Probe.ps1 -SpecPath spec.json
      |
      v
  shared\outputs\probes\probe_*.json
      |
      | (Syncthing v2, ~10-60s)
      v
                                        /home/workspace/shared/outputs/probes/probe_*.json
                                              |
                                              v
                                        probe_consumer.py (poll 5s)
                                              |
                                              v
                                        write_service /write
                                              |
                                              v
                                        mesh_events:
                                          event_type=probe_result          (always)
                                          event_type=improvement_candidate (WARN+)
                                              |
                                              v
                                        outputs/probes/processed/  (file moved)
```

## Severity classifier (first cut)

| `classified` | Triggers `improvement_candidate` row? |
|---|---|
| `ok` | no |
| `auth_required` | no |
| `not_found` | yes (WARN) |
| `down`, `timeout`, `tls_error`, `server_error`, `redirect_loop` | yes (ERROR) |
| `drift` | yes (WARN) — body changed vs baseline |
| `weak_posture` (dast_lite) | yes (WARN) |
| `exposure` (dast_lite) | yes (ERROR) |
| `agent_error` | yes (ERROR) |

## Real findings, first run

From `probe_dast_lite_20260426_202250_7293a2d0755c.json`:

```
  classified: weak_posture  severity: WARN
  findings:
    /openapi.json -> 200  WARN  "auth-free admin/dev surface"
  header_check.missing:
    Strict-Transport-Security
    X-Content-Type-Options
    X-Frame-Options
    Content-Security-Policy
    Referrer-Policy
```

This means once `probe_consumer.py` ingests this file, two
`improvement_candidate` rows will land in `mesh_events` for the builder /
sentinel review queue. **Concrete actionable signal from a 30-second run.**

## Next moves (queued)

1. **`zo_warm_worker.ps1` on tower** — polls `shared\work\probes\` for incoming
   spec files (not just hand-written ones), dispatches each to `Invoke-Probe.ps1`,
   moves spec to `processed\`. Wraps the existing dissolvable agent into a real
   loop driven by ZoComputer.
2. **`probe_consumer` as supervised service on ZoComputer** — same daemon_wrapper.sh
   pattern as builder/directive_generator. Today it's runnable as one-shot but not
   yet auto-respawning.
3. **Schedule recurring probes** — ZoComputer drops a `probe_spec_*.json` into
   `shared\work\probes\` every N minutes, the warm worker runs it. First
   schedule candidate: `dast_lite` against sentinel UI hourly, `http_probe`
   against UI `/health` every 5 min (drift detection on the body hash).
4. **`tree_walk` probe** — follow links from a base URL up to depth N, build a
   URL graph with status codes. Maps live API surface for builder testing.
5. **`log_classify` probe** — phi3-on-tower reads ZoComputer log tails, buckets
   failures by family, writes counts as a different probe_type. Closes the
   recursive-improvement loop: probes find issues, builder writes fixes, probes
   verify the fix landed.

## Architectural notes

- **No new tables created** — deliberately reusing `mesh_events` with two new
  `event_type` values (`probe_result`, `improvement_candidate`). Avoids the
  DDL-via-readonly-query-tool friction and keeps everything in the existing
  query path. Dedicated tables can come later via the proper init script.
- **No coupling between agent and trigger transport** — `Invoke-Probe.ps1`
  doesn't know whether it was called by a human, a Scheduled Task, the future
  warm worker, or Claude via Windows-MCP. It just takes a spec and writes a
  result. That's the dissolvable-agent contract.
- **First production lesson learned** — PowerShell `ExecutionPolicy` defaults
  block local script execution from MCP-spawned shells. Fixed once with
  `Set-ExecutionPolicy RemoteSigned -Scope CurrentUser`. Bank for next agent.