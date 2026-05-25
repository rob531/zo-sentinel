# Sentinel External API — 520 Fix (2026-04-26 ~21:35 UTC)

## Root cause

`/etc/zo/supervisord-user.conf`'s `[program:sentinel-external-api]` block had
`command=bash -c 'sleep infinity'`. Supervisord faithfully ran sleep, reported
`RUNNING`, but no API process existed. The Hosting tunnel forwarded to origin
`:8791`, found nothing listening, returned Cloudflare 520.

Same failure pattern as the historical `zo-sentinel-ui` zite issue (2026-04-21):
Hosting registers an entry, but the real launch command is never wired in.

## Fix

Launched `/home/workspace/zo_sentinel/sentinel_external_api.py` under the
`daemon_wrapper.sh` convention via `/home/workspace/logs/_start_sentinel_external_api.py`.
Runs independently of supervisord, auto-respawns on crash, survives ZoComputer
config regeneration.

Processes (verified):
```
  PID 5701  bash daemon_wrapper.sh sentinel_external_api .../sentinel_external_api.py
  PID 5707  python3 .../sentinel_external_api.py
```

Verification:
  - Internal:  `curl http://127.0.0.1:8791/v1/health` -> 200
  - External:  `https://sentinel-external-api-robinc.zocomputer.io/v1/health` -> 200
  - 520 from `/`, `/health`, `/docs` was actually "path not defined" — the API
    routes are namespaced under `/v1/`. Not a bug.

## Endpoints (per OpenAPI spec)

Real routes live under `/v1/...`. Pull the OpenAPI doc for the full list:
```
curl https://sentinel-external-api-robinc.zocomputer.io/openapi.json
```
First route confirmed: `GET /v1/health -> {"status":"ok","service":"sentinel_external_api","version":"1.0"}`

## To restart manually

```
python3 /home/workspace/logs/_start_sentinel_external_api.py
```
Idempotent — detects existing canonical process, exits cleanly if running.

## To stop

```
pkill -f /zo_sentinel/sentinel_external_api.py
pkill -f "daemon_wrapper.sh sentinel_external_api"
```
(Wrapper detects clean exit (rc=0) and stops respawning. Both PIDs needed.)

## Pattern lesson

Whenever a ZoComputer-hosted service returns 520 and the supervisord status
shows `RUNNING`, FIRST check the `command=` line in the program block. If it's
`sleep infinity` or any placeholder, the real service was never wired in.
Launching via `daemon_wrapper.sh` (matching the existing mesh service
convention) is the durable fix because it survives ZoComputer's periodic
regeneration of `/etc/zo/supervisord-user.conf`.

Candidates worth checking next time something else 520s:
```
grep -B1 -A2 'sleep infinity' /etc/zo/supervisord-user.conf
```