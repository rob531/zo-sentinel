# ZO-SENTINEL Supervisord Persistence Runbook
**Version:** 1.2.2  
**Date:** 2026-04-29  
**Purpose:** Resolve PROJECT_GOALS.md carryover #3 — supervised process persistence  
**Scope:** Programs in `/etc/zo/supervisord-user.conf`

---

## VERSION HISTORY

| Version | Date | Changes |
|---------|------|---------|
| 1.0 | 2026-04-29 | Initial. Authored by builder; produced wrong builder path. |
| 1.1 | 2026-04-29 | Cold-start cascade discipline added (§3.7), verification window 60s→120s, freshness bound 90s→180s. Lost on disk; only present in transcript. |
| 1.2 | 2026-04-29 | Builder path corrected from `/home/workspace/zo_sentinel/zo_sentinel_builder.py` to `/home/workspace/zo_mesh/zo_sentinel_builder.py` (fixes the FATAL we observed post-rollout). v1.1 cold-start guidance carried forward. New §9 "Applied / Deferred / Open" tracking ledger. |
| 1.2.1 | 2026-04-29 | Ledger correction: wisdom_synthesiser → escalation.ask() integration moved from "discussed" to APPLIED with production evidence (15 ladder calls in mesh_memory). Outstanding ladder integration is **zo_sentinel_builder.py only**. Robin caught the conflation; receipts confirmed. |
| 1.2.2 | 2026-04-29 | Restored full body of runbook after a v1.2.1 write accident truncated sections 1–8. §2.11 builder path is correct in this version. |

---

## CRITICAL: PATH FIX FOR EXISTING DEPLOYMENTS

If you have already applied v1.0 of this runbook, **you have a wrong builder path in your `/etc/zo/supervisord-user.conf`**. The `[program:zo_sentinel_builder]` block points at `/home/workspace/zo_sentinel/zo_sentinel_builder.py` which does not exist. The actual builder is at `/home/workspace/zo_mesh/zo_sentinel_builder.py`. Symptom: `supervisorctl status zo_sentinel_builder` shows `FATAL Exited too quickly`.

**Fix without reapplying the entire runbook:**

```bash
sudo sed -i 's|command=python3 /home/workspace/zo_sentinel/zo_sentinel_builder.py|command=python3 /home/workspace/zo_mesh/zo_sentinel_builder.py|' /etc/zo/supervisord-user.conf
sudo supervisorctl -c /etc/zo/supervisord-user.conf reread
sudo supervisorctl -c /etc/zo/supervisord-user.conf update zo_sentinel_builder
sudo supervisorctl -c /etc/zo/supervisord-user.conf status zo_sentinel_builder
```

If the manual builder instance is still running, supervisord's instance will FATAL again because of the lockfile / single-instance check. Stop the manual one first (only after any in-flight build completes — tail `/home/workspace/logs/zo_sentinel_builder.log` and look for `OK` or `FAILED` line):

```bash
pkill -f /home/workspace/zo_mesh/zo_sentinel_builder.py
rm -f /home/workspace/logs/zo_sentinel_builder.lock
sleep 4
sudo supervisorctl -c /etc/zo/supervisord-user.conf start zo_sentinel_builder
```

---

## SECTION 1: PRECONDITIONS

Before applying this runbook, verify the following:

### 1.1 Write Service Connectivity
```bash
curl -s -X POST http://127.0.0.1:8772/query -H "Content-Type: application/json" -d '{"sql": "SELECT service, last_heartbeat FROM service_health LIMIT 1"}' | python3 -m json.tool
```
Expected: JSON with `rows` array. If this fails, write_service is down — fix that first.

### 1.2 Existing Programs Registered
```bash
supervisorctl -c /etc/zo/supervisord-user.conf status
```
Expected: 13 baseline programs showing RUNNING.

### 1.3 Phase 1 Scripts Exist (CORRECTED PATHS)
```bash
for f in \
  /home/workspace/zo_mesh/trigger_watcher.py \
  /home/workspace/zo_mesh/zo_sentinel_builder.py \
  /home/workspace/zo_mesh/watchdog_daemon.py \
  /home/workspace/zo_sentinel/candidate_promoter_daemon.py \
  /home/workspace/zo_sentinel/candidate_npm_promoter.py \
  /home/workspace/zo_sentinel/candidate_github_promoter.py \
  /home/workspace/zo_sentinel/discovery_npm_paginator.py \
  /home/workspace/zo_sentinel/discovery_github_paginator.py \
  /home/workspace/zo_sentinel/registry_promoter_daemon.py \
  /home/workspace/zo_sentinel/fingerprint_runner_daemon_v3.py \
  /home/workspace/zo_sentinel/sentinel_directive_generator.py \
  /usr/bin/syncthing
do
  if [ -e "$f" ]; then echo "OK   $f"; else echo "MISS $f"; fi
done
```
If any line says `MISS`, **stop**.

### 1.4 Manual Daemons Potentially Running (will be stopped)
```bash
pgrep -af 'trigger_watcher|candidate_promoter|candidate_npm|candidate_github|discovery_npm|discovery_github|registry_promoter|fingerprint_runner_daemon|watchdog_daemon|zo_sentinel_builder' | grep -v grep
```

### 1.5 Lockfiles to Cleanse
```bash
ls -la /home/workspace/logs/*.lock 2>/dev/null
```

---

## SECTION 2: PHASE 1 PROGRAM BLOCKS

12 program blocks. The watchdog and zo_sentinel_builder blocks differ from v1.0 — zo_sentinel_builder uses the **correct path** `/home/workspace/zo_mesh/zo_sentinel_builder.py`.

### 2.1 syncthing
```ini
[program:syncthing]
command=/usr/bin/syncthing serve --no-browser --home=/home/robin/.config/syncthing
autostart=true
autorestart=true
stopsignal=TERM
stopasgroup=true
killasgroup=true
startretries=20
startsecs=5
stopwaitsecs=4
stdout_logfile=/dev/shm/syncthing.log
stdout_logfile_maxbytes=10MB
stdout_logfile_backups=5
stderr_logfile=/dev/shm/syncthing_err.log
stderr_logfile_maxbytes=10MB
stderr_logfile_backups=5
environment=HOME="/home/robin"
```

### 2.2 trigger_watcher
```ini
[program:trigger_watcher]
command=python3 /home/workspace/zo_mesh/trigger_watcher.py
directory=/home/workspace/zo_mesh
autostart=true
autorestart=true
stopsignal=TERM
stopasgroup=true
killasgroup=true
startretries=20
startsecs=5
stopwaitsecs=4
stdout_logfile=/dev/shm/trigger_watcher.log
stdout_logfile_maxbytes=10MB
stdout_logfile_backups=5
stderr_logfile=/dev/shm/trigger_watcher_err.log
stderr_logfile_maxbytes=10MB
stderr_logfile_backups=5
```

### 2.3 candidate_promoter_daemon
```ini
[program:candidate_promoter_daemon]
command=python3 /home/workspace/zo_sentinel/candidate_promoter_daemon.py
directory=/home/workspace/zo_sentinel
environment=PYTHONPATH="/home/workspace/zo_sentinel:/home/workspace/zo_mesh"
autostart=true
autorestart=true
stopsignal=TERM
stopasgroup=true
killasgroup=true
startretries=20
startsecs=5
stopwaitsecs=4
stdout_logfile=/dev/shm/candidate_promoter_daemon.log
stdout_logfile_maxbytes=10MB
stdout_logfile_backups=5
stderr_logfile=/dev/shm/candidate_promoter_daemon_err.log
stderr_logfile_maxbytes=10MB
stderr_logfile_backups=5
```

### 2.4 candidate_npm_promoter
```ini
[program:candidate_npm_promoter]
command=python3 /home/workspace/zo_sentinel/candidate_npm_promoter.py
directory=/home/workspace/zo_sentinel
environment=PYTHONPATH="/home/workspace/zo_sentinel:/home/workspace/zo_mesh"
autostart=true
autorestart=true
stopsignal=TERM
stopasgroup=true
killasgroup=true
startretries=20
startsecs=5
stopwaitsecs=4
stdout_logfile=/dev/shm/candidate_npm_promoter.log
stdout_logfile_maxbytes=10MB
stdout_logfile_backups=5
stderr_logfile=/dev/shm/candidate_npm_promoter_err.log
stderr_logfile_maxbytes=10MB
stderr_logfile_backups=5
```

### 2.5 candidate_github_promoter
```ini
[program:candidate_github_promoter]
command=python3 /home/workspace/zo_sentinel/candidate_github_promoter.py
directory=/home/workspace/zo_sentinel
environment=PYTHONPATH="/home/workspace/zo_sentinel:/home/workspace/zo_mesh"
autostart=true
autorestart=true
stopsignal=TERM
stopasgroup=true
killasgroup=true
startretries=20
startsecs=5
stopwaitsecs=4
stdout_logfile=/dev/shm/candidate_github_promoter.log
stdout_logfile_maxbytes=10MB
stdout_logfile_backups=5
stderr_logfile=/dev/shm/candidate_github_promoter_err.log
stderr_logfile_maxbytes=10MB
stderr_logfile_backups=5
```

### 2.6 discovery_npm_paginator
```ini
[program:discovery_npm_paginator]
command=python3 /home/workspace/zo_sentinel/discovery_npm_paginator.py
directory=/home/workspace/zo_sentinel
environment=PYTHONPATH="/home/workspace/zo_sentinel:/home/workspace/zo_mesh"
autostart=true
autorestart=true
stopsignal=TERM
stopasgroup=true
killasgroup=true
startretries=20
startsecs=5
stopwaitsecs=4
stdout_logfile=/dev/shm/discovery_npm_paginator.log
stdout_logfile_maxbytes=10MB
stdout_logfile_backups=5
stderr_logfile=/dev/shm/discovery_npm_paginator_err.log
stderr_logfile_maxbytes=10MB
stderr_logfile_backups=5
```

### 2.7 discovery_github_paginator
```ini
[program:discovery_github_paginator]
command=python3 /home/workspace/zo_sentinel/discovery_github_paginator.py
directory=/home/workspace/zo_sentinel
environment=PYTHONPATH="/home/workspace/zo_sentinel:/home/workspace/zo_mesh"
autostart=true
autorestart=true
stopsignal=TERM
stopasgroup=true
killasgroup=true
startretries=20
startsecs=5
stopwaitsecs=4
stdout_logfile=/dev/shm/discovery_github_paginator.log
stdout_logfile_maxbytes=10MB
stdout_logfile_backups=5
stderr_logfile=/dev/shm/discovery_github_paginator_err.log
stderr_logfile_maxbytes=10MB
stderr_logfile_backups=5
```

### 2.8 registry_promoter_daemon
```ini
[program:registry_promoter_daemon]
command=python3 /home/workspace/zo_sentinel/registry_promoter_daemon.py
directory=/home/workspace/zo_sentinel
environment=PYTHONPATH="/home/workspace/zo_sentinel:/home/workspace/zo_mesh"
autostart=true
autorestart=true
stopsignal=TERM
stopasgroup=true
killasgroup=true
startretries=20
startsecs=5
stopwaitsecs=4
stdout_logfile=/dev/shm/registry_promoter_daemon.log
stdout_logfile_maxbytes=10MB
stdout_logfile_backups=5
stderr_logfile=/dev/shm/registry_promoter_daemon_err.log
stderr_logfile_maxbytes=10MB
stderr_logfile_backups=5
```

### 2.9 fingerprint_runner_daemon_v3
```ini
[program:fingerprint_runner_daemon_v3]
command=python3 /home/workspace/zo_sentinel/fingerprint_runner_daemon_v3.py
directory=/home/workspace/zo_sentinel
environment=PYTHONPATH="/home/workspace/zo_sentinel:/home/workspace/zo_mesh"
autostart=true
autorestart=true
stopsignal=TERM
stopasgroup=true
killasgroup=true
startretries=20
startsecs=5
stopwaitsecs=4
stdout_logfile=/dev/shm/fingerprint_runner_daemon_v3.log
stdout_logfile_maxbytes=10MB
stdout_logfile_backups=5
stderr_logfile=/dev/shm/fingerprint_runner_daemon_v3_err.log
stderr_logfile_maxbytes=10MB
stderr_logfile_backups=5
```

### 2.10 sentinel_directive_generator
```ini
[program:sentinel_directive_generator]
command=python3 /home/workspace/zo_sentinel/sentinel_directive_generator.py
directory=/home/workspace/zo_sentinel
environment=PYTHONPATH="/home/workspace/zo_sentinel:/home/workspace/zo_mesh"
autostart=true
autorestart=true
stopsignal=TERM
stopasgroup=true
killasgroup=true
startretries=20
startsecs=5
stopwaitsecs=4
stdout_logfile=/dev/shm/sentinel_directive_generator.log
stdout_logfile_maxbytes=10MB
stdout_logfile_backups=5
stderr_logfile=/dev/shm/sentinel_directive_generator_err.log
stderr_logfile_maxbytes=10MB
stderr_logfile_backups=5
```

### 2.11 zo_sentinel_builder *** CORRECTED PATH IN v1.2 ***
```ini
[program:zo_sentinel_builder]
command=python3 /home/workspace/zo_mesh/zo_sentinel_builder.py
directory=/home/workspace/zo_mesh
environment=PYTHONPATH="/home/workspace/zo_mesh:/home/workspace/zo_sentinel"
autostart=true
autorestart=true
stopsignal=TERM
stopasgroup=true
killasgroup=true
startretries=20
startsecs=5
stopwaitsecs=4
stdout_logfile=/dev/shm/zo_sentinel_builder.log
stdout_logfile_maxbytes=10MB
stdout_logfile_backups=5
stderr_logfile=/dev/shm/zo_sentinel_builder_err.log
stderr_logfile_maxbytes=10MB
stderr_logfile_backups=5
```

### 2.12 watchdog (Python wrapper, v3.4)
```ini
[program:watchdog]
command=python3 /home/workspace/zo_mesh/watchdog_daemon.py
directory=/home/workspace/zo_mesh
autostart=true
autorestart=true
stopsignal=TERM
stopasgroup=true
killasgroup=true
startretries=20
startsecs=5
stopwaitsecs=4
stdout_logfile=/dev/shm/watchdog.log
stdout_logfile_maxbytes=10MB
stdout_logfile_backups=5
stderr_logfile=/dev/shm/watchdog_err.log
stderr_logfile_maxbytes=10MB
stderr_logfile_backups=5
```

**Important — dual-supervision risk.** Watchdog v3.4 covers some daemons by `pgrep` + auto-respawn. Once supervisord is also managing those daemons, watchdog's intervention can collide with supervisord's restart logic. After rollout, edit watchdog_daemon.py's coverage list (`DAEMON_PAIRS` array or equivalent) to remove every daemon now under supervisord. Watchdog should retain coverage only of daemons NOT yet promoted to supervisord (Phase 2 verify-then-add candidates and Phase 3 deferred items).

---

## SECTION 3: ROLLOUT SEQUENCE

### Step 1: Backup
```bash
sudo cp /etc/zo/supervisord-user.conf /etc/zo/supervisord-user.conf.bak.$(date -u +%Y%m%dT%H%M%SZ)
```

### Step 2: Stop Manual Daemon Instances
```bash
sudo pkill -f /home/workspace/zo_mesh/trigger_watcher.py
sudo pkill -f /home/workspace/zo_mesh/zo_sentinel_builder.py
sudo pkill -f /home/workspace/zo_mesh/watchdog_daemon.py
sudo pkill -f /home/workspace/zo_sentinel/candidate_promoter_daemon.py
sudo pkill -f /home/workspace/zo_sentinel/candidate_npm_promoter.py
sudo pkill -f /home/workspace/zo_sentinel/candidate_github_promoter.py
sudo pkill -f /home/workspace/zo_sentinel/discovery_npm_paginator.py
sudo pkill -f /home/workspace/zo_sentinel/discovery_github_paginator.py
sudo pkill -f /home/workspace/zo_sentinel/registry_promoter_daemon.py
sudo pkill -f /home/workspace/zo_sentinel/fingerprint_runner_daemon_v3.py
sudo pkill -f /home/workspace/zo_sentinel/sentinel_directive_generator.py
sudo pkill -f '/usr/bin/syncthing serve'

sudo rm -f /home/workspace/logs/*.lock
sleep 4
pgrep -af 'trigger_watcher|candidate_promoter|candidate_npm|candidate_github|discovery_npm|discovery_github|registry_promoter|fingerprint_runner_daemon|watchdog_daemon|zo_sentinel_builder|/usr/bin/syncthing serve' | grep -v grep
```
Last command should return nothing.

### Step 3: Append Phase 1 Program Blocks
Use the heredoc approach: copy each block from §2.1–2.12 above into one `tee -a` heredoc, then run. The blocks are word-for-word ready to paste.

### Step 4: Reread
```bash
sudo supervisorctl -c /etc/zo/supervisord-user.conf reread
```
Expected: each new program reported as `available`.

### Step 5: Update
```bash
sudo supervisorctl -c /etc/zo/supervisord-user.conf update
```

### Step 6: Verify Status (cold-start aware, from v1.1)
```bash
sudo supervisorctl -c /etc/zo/supervisord-user.conf status
```
Wait at least 120 seconds before checking heartbeats — cold-start cascades (see §3.7) take time when 12 daemons start in parallel with cross-dependencies.

### Step 7: Heartbeat Verification (single-line, phone-friendly)
```bash
python3 -c "import requests, json; r = requests.post('http://127.0.0.1:8772/query', json={'sql': \"SELECT service, last_heartbeat FROM service_health WHERE service IN ('syncthing','trigger_watcher','candidate_promoter_daemon','candidate_npm_promoter','candidate_github_promoter','discovery_npm_paginator','discovery_github_paginator','registry_promoter_daemon','fingerprint_runner_daemon_v3','sentinel_directive_generator','zo_sentinel_builder','watchdog')\"}, timeout=10); print(json.dumps(r.json(), indent=2, default=str))"
```
`syncthing` and `watchdog` won't appear (no service_health post). Verify them via `pgrep -af '/usr/bin/syncthing serve'` and `pgrep -af 'watchdog_daemon.py'` separately.

### §3.7 COLD-START CASCADE DISCIPLINE (v1.1)

When 12 daemons start simultaneously, cascading warmup latencies compound. Builder needs inference_router warm; signal_analyser needs write_service. Rule of thumb: `total_warmup_window = max_individual_startup * dependency_depth`. Today: ~60s individual * dependency depth 2 = ~120s safe minimum before checking heartbeats.

Supervisord's RUNNING state (with `startsecs=5`) is more reliable than port-bind probes. If any daemon shows BACKOFF on first `supervisorctl status`, wait another 30s before declaring failure. supervisord's `startretries=20` is retrying internally.

---

## SECTION 4: ROLLBACK

### 4.1 Restore Backup
```bash
sudo cp /etc/zo/supervisord-user.conf.bak.<TIMESTAMP> /etc/zo/supervisord-user.conf
sudo supervisorctl -c /etc/zo/supervisord-user.conf reread
sudo supervisorctl -c /etc/zo/supervisord-user.conf update
```

### 4.2 Partial Rollback
Comment out a single offending `[program:NAME]` block (prefix every line with `;`), then reread + update.

### 4.3 Manual Restart Path (Emergency)
Use the existing recovery scripts — they remain valid as fallback:
- `python3 /home/workspace/logs/_locate_and_start_syncthing_v2.py`
- `python3 /home/workspace/logs/_launch_breadth_daemons.py`
- `python3 /home/workspace/logs/_relaunch_post_reboot.py`
- `zm go` for full bootstrap

---

## SECTION 5: TEST PLAN (Respawn Verification)

After rollout, kill -9 one daemon and verify supervisord respawns it within 10 seconds. Repeat for one or two more for confidence.

```bash
pid=$(supervisorctl -c /etc/zo/supervisord-user.conf status candidate_github_promoter | awk '{print $4}' | tr -d ',')
kill -9 $pid
sleep 8
supervisorctl -c /etc/zo/supervisord-user.conf status candidate_github_promoter
```
Expected: NEW pid, RUNNING, uptime <10s.

---

## SECTION 6: PHASE 2 AUDIT (verify-then-add)

For each Phase 2 daemon, before adding:

1. Confirm canonical script path: `pgrep -af <daemon_name>`. If you see the path, that's the canonical command. If you see two paths, investigate the dual-instance before adding.
2. Confirm 5+ minutes of consistent fresh heartbeats in `service_health`.
3. Check for port binding: `ss -tlnp | grep <port>`.
4. Copy the v1.2 template, update `command=`, `directory=`, and `[program:NAME]`, append, reread, update.
5. `build_watcher_api` is NOT a Phase 2 candidate — already registered as `zo-build-feed`. Adding a duplicate would crash both.

Phase 2 daemons: inference_router, signal_bridge, signal_analyser, trust_synthesiser, t2_consumer, pipeline_bridge, manager_agent, ecosystems_metadata_fetcher, gate_scheduler, liveness_probe.

---

## SECTION 7: NOTES ON sentinel-external-api PLACEHOLDER

The `sentinel-external-api` program currently runs `command=bash -c 'sleep infinity'` to reserve port 8791. When the real service is ready, **edit the command line in place via sed** rather than deleting and recreating the block. That preserves the port reservation across the rollout.

```bash
sudo sed -i 's|command=bash -c .sleep infinity.|command=python3 /home/workspace/zo_sentinel/sentinel_external_api.py|' /etc/zo/supervisord-user.conf
sudo supervisorctl -c /etc/zo/supervisord-user.conf reread
sudo supervisorctl -c /etc/zo/supervisord-user.conf restart sentinel-external-api
```

---

## SECTION 8: CHECKLIST

Tick each before declaring rollout complete:

- [ ] Backup of `/etc/zo/supervisord-user.conf` exists at `*.bak.<timestamp>`
- [ ] All 12 script paths verified `OK` per §1.3 (CORRECTED PATHS)
- [ ] All manual instances stopped per §3.2 (verified empty `pgrep -af ...`)
- [ ] Heredoc append to `/etc/zo/supervisord-user.conf` completed without error
- [ ] `supervisorctl reread` reported all 12 programs as `available`
- [ ] `supervisorctl update` started all 12 programs
- [ ] Waited at least 120 s for cold-start cascade to complete
- [ ] `supervisorctl status` shows all 12 as `RUNNING` (no FATAL, no BACKOFF)
- [ ] Heartbeat query confirms 10 mesh-side daemons heartbeating fresh (`seconds_ago < 180`)
- [ ] Syncthing process visible in `pgrep -af '/usr/bin/syncthing serve'`
- [ ] Watchdog process visible in `pgrep -af 'watchdog_daemon.py'`
- [ ] §5 kill-respawn test passed for at least one daemon
- [ ] watchdog_daemon.py DAEMON_PAIRS list updated to remove now-supervisord-managed daemons (§2.12 dual-supervision warning)
- [ ] PROJECT_GOALS.md carryover #3 marked closed

---

## SECTION 9: APPLIED / DEFERRED / OPEN — LIVE TRACKING LEDGER

**Purpose:** Prevent the "phantom-completed item" failure mode from temporal_checks.md — every CTO-scope work item has a clear status: APPLIED (in production), DEFERRED (decided to do later), or OPEN (not yet decided / queued for next session).

### APPLIED (in production)

- [APPLIED 2026-04-25] **wisdom_synthesiser → escalation.ask() ladder integration.** `/home/workspace/zo_mesh/wisdom_synthesiser.py` calls `escalation.ask("wisdom_synthesis", ...)` instead of `ollama_synthesise()` directly. Five iterations of escalation.py since (v0.1 through v0.5). **Production evidence:** mesh_memory `agent_id='escalation.router'` rows with `task_type='wisdom_synthesis'` show 15 production calls — 10 successful zo:google/gemini-3.1-pro-preview, 2 failed Gemini-3.1-pro (Zo paid-tier 402 Payment Required), 2 failed zo:openai/gpt-5.4 (same 402), and 1 successful MiniMax-M2.7 (today's v0.5-retargeted run). v0.5 specifically fixed the case where wisdom_synthesis was getting stuck in the paid-Zo rung 9-12 window once Zo started returning 402; retargeted the start tier to rung 0 (MiniMax direct, free).
- [APPLIED 2026-04-29] supervisord-user.conf v1.0 rollout — 12 Phase 1 program blocks added. 11 of 12 RUNNING; `zo_sentinel_builder` FATAL due to wrong path (fixed in v1.2; user pending re-apply).
- [APPLIED 2026-04-29] `/home/workspace/zo_mesh/zo_lifecycle.py` direct write — RLSD environment-direction signal foundation. Self-test PASS confirmed via /home/workspace/logs/lifecycle/zo_lifecycle_selftest-11039.jsonl.
- [APPLIED 2026-04-29] `/home/workspace/zo_sentinel/signal_training_corpus.py` via builder + smoke-rescue — RLSD teacher-magnitude data capture. Smoke PASS on attempt 2 after attempt 1's f-string SyntaxError.
- [APPLIED 2026-04-29] `temporal_checks.md` failure modes #7 (manual-launch-doesn't-survive-reboot), #8 (zm-go-cron-mislabel), #9 (file-bridge-not-network-bridge) added.
- [APPLIED 2026-04-29] `_relaunch_post_reboot.py` recovery script for the 4 daemons not in `zm go`'s static list.
- [APPLIED 2026-04-29] `_launch_breadth_daemons.py` for the 3 npm/github daemons (idempotent).
- [APPLIED 2026-04-29] trigger_watcher v1.8 with `launch_breadth_daemons` and `check_breadth_daemons` whitelists.
- [APPLIED 2026-04-29] watchdog v3.4 with corrected pgrep canonical-path matching.

### DEFERRED (decided not-to-do-now, with reason)

- [DEFERRED 2026-04-29] **zo_sentinel_builder → escalation.ask() ladder integration.** **Why:** the ladder is well-tested in production for wisdom_synthesiser (15 calls, see APPLIED above) but `zo_sentinel_builder.py` v1.9.5 still has its own MiniMax → Ollama cascade that bypasses escalation.py entirely. Today's MiniMax-only path produced two garbage outputs (lifecycle stub + supervisord runbook stub) that I had to direct-write replacements for. The fix is precise: replace the model-call site in zo_sentinel_builder.py (currently `MiniMax → Ollama llama3.2:3b`) with `escalation.ask(task_type='generate', ...)` while preserving the smoke-fail-rescue logic. Estimated effort: 1–2 hours. Best done in a clean session — builder is critical infra. Ladder integration would benefit ALL future builds. **This is the only outstanding ladder integration.**
- [DEFERRED 2026-04-29] `zm go` cold-start hardening (sections 4 + 16 retry-with-backoff). **Why:** v1.1 of this runbook captures the necessary discipline so we don't fail the supervisord rollout on the same trap; `zm go` will continue to false-fail on cold boots until patched, but it's not blocking real work.
- [DEFERRED 2026-04-29] Tower-side harvester for lifecycle JSONL. **Why:** the foundation (zo_lifecycle.py) is in place but no daemon actually imports it yet; harvester is premature without producers. Wire emitters into 2–3 daemons first as a proof, then build the harvester.
- [DEFERRED 2026-04-29] write_service heartbeat-thread-crash diagnosis. **Why:** main loop is alive and writes are landing; only the heartbeat thread is dead. Real fix is reading write_service source and restarting just the heartbeat thread — not urgent.
- [DEFERRED 2026-04-29] discovery_github_paginator zero-candidates root cause. **Why:** likely GitHub unauthenticated rate-limit (10 req/hr) but unconfirmed. Fix is `GITHUB_TOKEN` in environment but needs token rotation discipline.
- [DEFERRED 2026-04-29] signal_analyser verdict-uniformity bug (5/6 signals show 1 distinct value across all MCPs). **Why:** dirgen autonomously queued `signal_flatness_alarm` to address this; let the autonomous loop produce a candidate first.
- [DEFERRED 2026-04-29] BRIDGES.md amendment to clarify that the 12:02Z-section's "two Syncthing folders" is wrong (one is Unison/Resilio with Alpha/Beta peers). **Why:** documentation hygiene; doesn't block work.

### OPEN (decision still required)

- [OPEN] Custom signal-scoring model training plan — RLSD vs plain RLVR vs SFT-distillation. **Decision needed:** which technique class first. Initial recommendation in this session was "RLVR for signal scoring, RLSD for wisdom synthesis later". Robin shared the arXiv 2604.03128v2 paper on RLSD; technique alignment with our use cases is captured but no commitment yet. Required to start: training-data backfill script (re-score existing ~960 distinct-scored MCPs through escalation ladder frontier tier).
- [OPEN] Builder-builder meta-service per PROJECT_GOALS — watches GENERATION_FAILURES.md, BUILD_MANIFEST.md, smoke_fail patterns; produces patches to KNOWLEDGE_BASE.md, SENTINEL_DIRECTIVE_SCHEMA.md, antipattern lists. Discussed across multiple sessions; not yet scoped as a directive.
- [OPEN] Bearer auth toggle on `zo_mcp_server.py` per `zo_mcp_server_auth_patch.md`. **Status:** auth probe verdict was effectively PASS (stub 001 confirmed the platform layer is open; FastMCP middleware is the only gate, and `_MCP_SECRET = ""` so it's currently disabled). Runbook is ready to apply but Robin's call when.
- [OPEN] `Invoke-Check.ps1 v0.4` (recurring-schedule fix) — v0.3 archives every stub to `processed/` after first fire; recurring stubs (002, 003) only fire once. Fix pattern documented but not yet shipped.
- [OPEN] `outcome_consumer.py` ZO-side daemon to ingest tower check outcomes into `mesh_events`. Sibling to `probe_consumer.py`. Blocked on Invoke-Check.ps1 v0.4.
- [OPEN] Tower ZoWarmWorker.ps1 splice for the check-stub infrastructure. **Status:** stub 001 (auth_probe) ran successfully via direct Invoke-Check call. For 002/003 to run on schedule, the 8-line block from `INTEGRATE_INTO_ZOWARMWORKER.md` needs to be pasted into tower's existing ZoWarmWorker.ps1.

### Conventions

- **APPLIED**: in production. Should be observable on disk / in DB / in process list right now.
- **DEFERRED**: decided not to do now, with explicit reason. Should be revisited periodically.
- **OPEN**: decision still pending. Either Robin needs to call it, or new information is needed.

**End-of-session discipline:** Claude must, at the end of every working session, update this section with anything that moved between states, and explicitly call out any item that was discussed but not changed.

**Specificity discipline:** when there are two things in the same architectural family (e.g., "the ladder" can refer to wisdom_synthesiser usage or zo_sentinel_builder usage), name the consumer explicitly every time. "Ladder integration" is ambiguous; "wisdom_synthesiser ladder integration" or "builder ladder integration" is not.

---

## APPENDIX A: PHASE 3 DEFER LIST

Do NOT auto-register without manual restart and 5+ minutes of fresh heartbeats first:

`data_velocity`, `self_diagnostics`, `world_article_feeder`, `threat_intel_ingestor`, `write_service` (heartbeat thread crashed but main loop alive — do not register, would conflict on port 8772), `risk_ranker`, `gate_orchestrator`, `wisdom_synthesiser`, `anti_entropy`, `mcp_scanner`, `attestation_engine`, `probe_consumer`, `watch_shared`, `rug_pull_monitor`, `mcp_reference_servers_ingestor`, `mcp_registry_ingestor`, `otx_ingestor`.

## APPENDIX B: PHASE 4 RETIRE LIST

Do NOT register — abandoned per PROJECT_GOALS.md or replaced by newer versions:

`build_watchdog` (replaced by watchdog v3.4), `decision_emitter_daemon`, `fingerprint_runner_daemon` v1 (replaced by v3), `autonomous_tasks`, `gemini_embedding_router`, `context_service`, `mesh` (old name; replaced by `zo-mesh`), `sentinel_external_api` (heartbeat null entry; the placeholder `sentinel-external-api` program is fine to keep).

---

*End of v1.2.2 runbook.*