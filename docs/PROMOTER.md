# Promoter: proposed/ -> pending/

`zo_sentinel/promoters/proposed_to_pending_promoter.py` is the downstream
half of the Phase 0b directive pipeline. It drains `directives/proposed/`
(produced by `sentinel_directive_generator_goose.py`) into
`directives/pending/` (consumed by `goose_runner.py`).

Implements ROLLOUT.md Step 4b.

## Why this exists

PR #1 wired the directive_architect to write *proposals* into a sandbox
directory that `goose_runner` does **not** watch. Without something draining
that sandbox, the Phase 0b daemon hits its depth cap and logs forever:

```
proposed/ depth 40 >= cap 40; skipping cycle
```

This promoter is the drain. It is dormant until supervisord runs it; until
then `--once` is the manual unblock lever.

## Modes

| Mode | Flag | When to use |
| --- | --- | --- |
| Daemon | (default) | Long-running drain, supervisord-managed |
| One-shot | `--once` | Manual unblock; CI; ad-hoc inspection |
| Dry-run | `--dry-run` (requires `--once`) | Preview a pass without moving anything |

## Promotion rules

For each `directives/proposed/*.json`:

1. **TTL guard**: only consider files older than `--min-age-secs`
   (default `60`, env `PROMOTER_MIN_AGE_SECS`). Gives a human time to
   tag/skip a fresh proposal before it auto-promotes.
2. **Skip marker**: if `<basename>.skip` (zero-byte) exists alongside,
   the file is held out indefinitely.
3. **Validate**: shape-check the directive (required fields, valid handler,
   valid complexity, description >= 50 chars). Mirrors the canonical
   `_validate()` semantics from `zo_sentinel/mcp_servers/directive_mcp.py`
   and `sentinel_directive_generator.validate_directive`. Invalid files
   are renamed to `<name>.rejected` so they won't be reconsidered.
4. **Atomic move**: `os.replace(src, pending/<basename>)`. If the
   destination already exists (extremely rare with hash-based naming),
   the source is left in place and the cycle logs the collision.
5. **Per-cycle cap**: `--max-per-cycle` (default `10`, env
   `PROMOTER_MAX_PER_CYCLE`) so a deep backlog drains over multiple
   cycles instead of dumping into pending all at once.

## CLI

```
python -m zo_sentinel.promoters.proposed_to_pending_promoter \
    [--proposed-dir PATH] [--pending-dir PATH] \
    [--poll-secs INT] [--min-age-secs INT] \
    [--max-per-cycle INT] [--once] [--dry-run]
```

Default `--proposed-dir` / `--pending-dir` resolve repo-relative
(`<repo>/directives/{proposed,pending}/`) on dev hosts, and fall back to
`/home/workspace/zo_sentinel/directives/{proposed,pending}/` on the
tower if the repo-relative paths don't exist.

## Telemetry

- One-line cycle summary at INFO level:
  ```
  cycle: scanned=N eligible=M promoted=P rejected=Q skipped=R
  ```
- Heartbeat at `HEARTBEAT_SECS` (60s) cadence in daemon mode.
- File logging to `/home/workspace/logs/proposed_to_pending_promoter.log`
  when writable; stderr otherwise (Windows / CI).

## Manual unblock (current 40-file backlog)

Preview first:

```bash
python -m zo_sentinel.promoters.proposed_to_pending_promoter \
    --once --dry-run \
    --proposed-dir /home/workspace/zo_sentinel/directives/proposed \
    --pending-dir  /home/workspace/zo_sentinel/directives/pending \
    --max-per-cycle 40
```

Then live:

```bash
python -m zo_sentinel.promoters.proposed_to_pending_promoter \
    --once \
    --proposed-dir /home/workspace/zo_sentinel/directives/proposed \
    --pending-dir  /home/workspace/zo_sentinel/directives/pending \
    --max-per-cycle 40
```

`--max-per-cycle 40` is intentional for the unblock pass — it's a one-shot.
In daemon mode keep the default (10) so `goose_runner` is never deluged.

## Supervisord block (NOT shipped — paste when ready)

Add to `/etc/zo/supervisord-user.conf`:

```ini
[program:proposed_to_pending_promoter]
command=/usr/bin/python3 -m zo_sentinel.promoters.proposed_to_pending_promoter
directory=/home/workspace/zo_sentinel
environment=
    PROMOTER_POLL_SECS="60",
    PROMOTER_MIN_AGE_SECS="60",
    PROMOTER_MAX_PER_CYCLE="10",
    PYTHONPATH="/home/workspace/zo_sentinel:/home/workspace"
autostart=true
autorestart=true
startsecs=5
stopwaitsecs=10
stdout_logfile=/home/workspace/logs/proposed_to_pending_promoter.stdout.log
stderr_logfile=/home/workspace/logs/proposed_to_pending_promoter.stderr.log
stdout_logfile_maxbytes=10MB
stderr_logfile_maxbytes=10MB
stdout_logfile_backups=3
stderr_logfile_backups=3
user=root
priority=420
```

After adding:

```bash
sudo supervisorctl reread
sudo supervisorctl update proposed_to_pending_promoter
sudo supervisorctl status proposed_to_pending_promoter
```

`priority=420` puts it just after the directive generators so it picks up
fresh proposals on the next tick. `startsecs=5` lets it stabilise — the
first cycle runs immediately on start.

## Dormant status

This module is dormant until the supervisord block above is installed.
No code in this repo registers it. The daemon will not run on the tower
just because this PR merges — Robin installs the block when ready.
