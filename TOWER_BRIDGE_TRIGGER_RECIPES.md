# Tower ↔ ZoComputer Bridge — Trigger-File Recipes

**Created:** 2026-04-21  
**Purpose:** Document the exact operational actions the tower-side Claude Desktop bridge should be able to trigger on ZoComputer via the `/shared/triggers/` file-passing convention. This is the operational companion to the design sketched in TOWER_ARRIVAL_PREP.md.

---

## Why this file exists

The morning of 2026-04-21 surfaced two concrete restart operations in the space of 30 minutes:

1. **Full-stack recovery.** Multiple services had wedged overnight (WriteService down, directive generator timing out on both MiniMax and Ollama). The right answer was `zm go` — the idempotent 18-step recovery script in `/home/workspace/zo_mesh/go.sh`. It brought everything back in one command.
2. **Single-daemon patch deploy.** After patching `sentinel_directive_generator.py` (Strategy 0 to strip reasoning-mode preambles from MiniMax), the running process needed to be cycled to pick up the new code. `pkill -f sentinel_directive_generator.py` killed both the python process AND its `daemon_wrapper.sh` parent (because the wrapper's argv contained the `.py` path). Net effect: nothing respawned.

Both operations are routine. Both should be one click from Claude Desktop on the tower, not a terminal dance on ZoComputer.

---

## The pattern

Shared directory on both sides, synced by Syncthing:

```
/shared/triggers/
  <trigger_name>.request          ← tower writes; watcher on ZoComputer reads, deletes
  <trigger_name>.result.<ts>      ← watcher writes; tower reads
  audit.log                       ← append-only log of every trigger fire
```

Flow:

1. Claude Desktop (or you) drops `<trigger_name>.request` into `/shared/triggers/`
2. Syncthing mirrors it to ZoComputer within ~1-2s
3. A small watcher daemon on ZoComputer (supervisord-managed, ~100 lines) notices the file, validates the trigger against a whitelist, runs the associated command, captures stdout+stderr+exit-code, writes `<trigger_name>.result.<timestamp>`, deletes the request, and appends an entry to `audit.log`
4. Syncthing mirrors the result back to the tower
5. Claude Desktop reads the result and reports back to you

End-to-end: 5-10 seconds for a light op like a single-daemon restart; 45-60 seconds for a full `zm go`.

---

## Initial whitelist

Start small. Every entry here is a pre-written shell command with no user-supplied arguments. The request file can carry a `reason` field for audit purposes but cannot alter the command that runs.

### `zm_go.request`

**Command:** `/bin/zsh -c 'cd /home/workspace/zo_mesh && zsh go.sh'` (or whatever alias `zm go` actually expands to — confirm on day 1)  
**Purpose:** Full mesh recovery. Safe to run repeatedly; go.sh is idempotent by design.  
**Timeout:** 120 seconds.  
**When to fire:** multiple services wedged, WriteService down, pipeline bridge stalled, "everything feels off" state.  
**Result shape:** full stdout of go.sh, which ends with a SUMMARY block listing port statuses and daemon instance counts. Tower-side Claude should parse the summary and highlight any `not responding` or `failed` entries.

### `restart_directive_gen.request`

**Command:** targeted relaunch that avoids the pkill-wrapper trap:
```
pgrep -f 'python3 /home/workspace/zo_sentinel/sentinel_directive_generator\.py$' | xargs -r kill; \
sleep 2; \
pgrep -f 'python3 /home/workspace/zo_sentinel/sentinel_directive_generator\.py$' || \
  nohup bash /home/workspace/zo_mesh/daemon_wrapper.sh \
    sentinel_directive_generator \
    /home/workspace/zo_sentinel/sentinel_directive_generator.py \
  >> /home/workspace/logs/sentinel_sentinel_directive_generator.log 2>&1 &
```
**Purpose:** Pick up a freshly-patched `sentinel_directive_generator.py` without cycling the whole mesh.  
**Timeout:** 15 seconds.  
**When to fire:** immediately after a `zo_write_file` edit to the generator.  
**Result shape:** new PID if started, or existing PID if the pkill missed (possible if the wrapper already respawned). Tower-side Claude confirms by reading the log tail.

### `restart_<service>.request` (generalized, one per daemon)

Same pattern as `restart_directive_gen.request` but templated per service. Initial set worth pre-writing:
- `restart_write_service.request` — restarts WriteService on port 8772
- `restart_builder.request` — restarts `zo_sentinel_builder.py`
- `restart_signal_bridge.request` — restarts `signal_bridge.py`
- `restart_manager.request` — restarts `run_manager.py daemon`

Each uses the same anchored-pgrep pattern so the wrapper survives the kill.

### `health_check.request`

**Command:** `python3 /home/workspace/zo_mesh/print_health.py` (or equivalent) — the same data `zo_agent_health` returns, but dumped to stdout so the tower gets a snapshot without an MCP call.  
**Purpose:** low-cost status probe that also creates an audit record.  
**Timeout:** 10 seconds.

### `fetch_directive_log.request`

**Command:** `tail -n 200 /home/workspace/logs/sentinel_sentinel_directive_generator.log`  
**Purpose:** cheap way to see what the directive gen is doing without paging through MCP tools.  
**Timeout:** 5 seconds.

---

## Safety rails

1. **Strict whitelist.** The watcher has a hardcoded dict mapping trigger names to commands. Any `*.request` filename not in the dict is logged and ignored (with a result file explaining why). Never do string-interpolation from the request file into the shell command.
2. **One command per trigger.** Each whitelist entry is a single pre-composed command — no parameters pulled from the request file's JSON. If you need variation (e.g. "restart ANY daemon"), make it N whitelist entries, not one parametric entry.
3. **Rate limit.** Same trigger can't fire more than once per 30 seconds. Prevents feedback loops if a result file somehow ends up looking like a request file.
4. **Explicit timeouts.** Every command runs under `timeout N` with an N shorter than Syncthing's typical retry window. No zombie processes.
5. **Audit log.** Append-only, one line per trigger fire: timestamp, trigger name, exit code, duration, request-file hash. Grep for anomalies.
6. **Request files are deleted immediately.** Before the command runs, the watcher unlinks the request. Prevents re-execution if Syncthing hiccups.
7. **Results carry everything.** Exit code, stdout, stderr, start time, duration, hostname. If something failed, the tower has enough to reason about it.
8. **No secrets in trigger files.** Commands reference env vars (`$MINIMAX_API_KEY` etc) which are loaded by the watcher from `.zo_env` at startup. Tower never has to know secrets.

---

## Watcher implementation sketch

```python
# /home/workspace/zo_mesh/trigger_watcher.py
# Supervisord-managed. Polls /shared/triggers/ every 2s.

import time, os, subprocess, json, hashlib
from datetime import datetime, timezone
from pathlib import Path

TRIGGERS = Path("/shared/triggers")
AUDIT = TRIGGERS / "audit.log"
RATE_LIMIT_SECS = 30
_last_fired: dict[str, float] = {}

# Load secrets once at startup
from dotenv import load_dotenv
load_dotenv("/home/workspace/zo_mesh/.zo_env")

WHITELIST = {
    "zm_go": {
        "cmd": ["/bin/zsh", "-c", "cd /home/workspace/zo_mesh && zsh go.sh"],
        "timeout": 120,
    },
    "restart_directive_gen": {
        "cmd": ["/bin/bash", "-c",
            "pgrep -f 'python3 /home/workspace/zo_sentinel/sentinel_directive_generator\\.py$' "
            "| xargs -r kill; sleep 2; "
            "pgrep -f 'python3 /home/workspace/zo_sentinel/sentinel_directive_generator\\.py$' "
            "|| nohup bash /home/workspace/zo_mesh/daemon_wrapper.sh "
            "sentinel_directive_generator "
            "/home/workspace/zo_sentinel/sentinel_directive_generator.py "
            ">> /home/workspace/logs/sentinel_sentinel_directive_generator.log 2>&1 &"],
        "timeout": 15,
    },
    # ... more entries ...
}

def audit(trigger, exit_code, duration, req_hash):
    with AUDIT.open("a") as f:
        f.write(json.dumps({
            "ts": datetime.now(timezone.utc).isoformat(),
            "trigger": trigger, "exit": exit_code,
            "duration_s": round(duration, 2), "req_hash": req_hash
        }) + "\n")

def process_request(req: Path):
    name = req.stem  # e.g. 'zm_go' from 'zm_go.request'
    now = time.monotonic()
    if name in _last_fired and now - _last_fired[name] < RATE_LIMIT_SECS:
        write_result(name, exit_code=-1, stdout="",
                     stderr=f"rate-limited (last fire {now - _last_fired[name]:.1f}s ago)")
        req.unlink()
        return
    if name not in WHITELIST:
        write_result(name, exit_code=-1, stdout="",
                     stderr=f"trigger not in whitelist")
        req.unlink()
        return
    spec = WHITELIST[name]
    req_hash = hashlib.sha256(req.read_bytes()).hexdigest()[:12]
    req.unlink()  # delete BEFORE running, to prevent re-exec
    start = time.monotonic()
    try:
        result = subprocess.run(spec["cmd"], capture_output=True, text=True,
                                timeout=spec["timeout"])
        exit_code = result.returncode
        stdout, stderr = result.stdout, result.stderr
    except subprocess.TimeoutExpired as e:
        exit_code = -2
        stdout, stderr = e.stdout or "", e.stderr or "TIMEOUT"
    duration = time.monotonic() - start
    _last_fired[name] = now
    write_result(name, exit_code, stdout, stderr, duration)
    audit(name, exit_code, duration, req_hash)

def write_result(name, exit_code, stdout, stderr, duration=0):
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    (TRIGGERS / f"{name}.result.{ts}").write_text(json.dumps({
        "trigger": name, "exit_code": exit_code, "duration_s": round(duration, 2),
        "stdout": stdout, "stderr": stderr,
        "completed_at": datetime.now(timezone.utc).isoformat(),
    }, indent=2))

def main():
    TRIGGERS.mkdir(parents=True, exist_ok=True)
    while True:
        for req in TRIGGERS.glob("*.request"):
            try:
                process_request(req)
            except Exception as e:
                # never crash the watcher; log and move on
                audit(req.stem, -99, 0, str(e)[:100])
        time.sleep(2)

if __name__ == "__main__":
    main()
```

~100 lines. Test coverage: fire each whitelist entry manually, verify result file shape, verify rate limit, verify non-whitelisted trigger is rejected, verify timeout fires correctly.

---

## Lessons banked during 2026-04-21 rescue

1. **`pkill -f <name>.py` kills the wrapper too.** The wrapper's argv contains the `.py` path as a positional argument, so `-f` matches it. Safer patterns: anchor the regex with `$` to match only the python invocation, or kill by PID from a specific pgrep.
2. **MiniMax-M2.7 started returning `<think>...</think>` blocks mid-April 2026.** No changelog notification, no version bump. The directive generator's JSON parser had no defense against reasoning-mode preambles. Patched 2026-04-21 by adding Strategy 0 to `generate_directives()`. General rule: every external API response is an untrusted contract that may drift.
3. **Idempotency covers internal state, not external contracts.** System idempotency (safe to re-run the same command, safe to re-process the same input) is orthogonal to robustness against provider-side format changes. Both are needed. Code that was idempotent yesterday can still break today if its inputs change shape.
4. **`zm go` is the right hammer and it's fast enough.** 45-60 seconds for a full cycle. Don't over-engineer targeted restarts when the full cycle is well-tested and idempotent — reserve targeted restarts for cases where a full cycle would disrupt something expensive (long-running analysis, active build).

---

## Open questions for tower day-one

1. **Shared directory mount.** Is `/shared/triggers/` on ZoComputer the same physical directory as the tower's view, via Syncthing, or is there a permissions boundary to worry about? Default assumption: Syncthing handles it transparently, same path on both sides.
2. **Watcher process management.** supervisord is the natural host. Add it to `/etc/zo/supervisord-user.conf` and to `go.sh` as section 19 so it survives reboots.
3. **Request provenance.** Do we need to prove a request came from the tower vs being dropped by something else on ZoComputer? For solo use, probably not; for auditability later, add a signed-request field in the JSON.
4. **Result retention.** How long do result files live in `/shared/triggers/`? Default: cleanup after 7 days. Audit log is kept forever.

---

## What day-one tower setup needs to add

- [ ] Create `/shared/triggers/` on both sides
- [ ] Install `trigger_watcher.py` on ZoComputer, supervised, autostart
- [ ] Add whitelist entries for initial set (`zm_go`, `restart_directive_gen`, `restart_write_service`, `restart_builder`, `health_check`, `fetch_directive_log`)
- [ ] Smoke test each trigger from tower-side by writing a `.request` file manually and verifying the `.result.<ts>` appears
- [ ] Add a Claude Desktop-friendly helper: a small script on the tower that wraps "write request → wait for result → display" so Claude Desktop can invoke triggers as single MCP filesystem operations
- [ ] Document the whitelist + safety rails in a README that future-Robin can re-read after a month away