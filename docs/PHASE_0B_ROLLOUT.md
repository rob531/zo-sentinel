# Phase 0b — Goose-driven Directive Architect

Three additive files. Zero modifications to running code. Cannot break Goose-Architect (architect.yaml) or the build chain (goose_runner → ZoBuilder).

## What's in this bundle

| File | Destination on ZoComputer | Purpose |
|---|---|---|
| `goose_recipes/directive_architect.yaml` | `/home/workspace/zo_sentinel/goose_recipes/directive_architect.yaml` | New recipe. Sibling of architect.yaml. |
| `zo_sentinel/mcp_servers/directive_mcp.py` | `/home/workspace/zo_sentinel/mcp_servers/directive_mcp.py` | New MCP server. Sibling of builder_mcp.py. |
| `zo_sentinel/sentinel_directive_generator_goose.py` | `/home/workspace/zo_sentinel/sentinel_directive_generator_goose.py` | New side-by-side daemon. NOT yet in supervisord. |

## Why this can't break Goose

1. **architect.yaml is untouched.** The new recipe lives at a different path. Goose-Architect's existing flow (delegate_to_builder → ZoBuilder) is byte-for-byte unchanged.
2. **builder_mcp.py is untouched.** The new MCP server lives in a different file. Both servers can coexist; Goose loads them per-recipe via the `extensions:` block.
3. **goose_runner.py is untouched.** It still watches `directives/pending/`. The new daemon writes to `directives/proposed/`, a different directory. goose_runner can never accidentally pick up an un-promoted proposal.
4. **sentinel_directive_generator.py is untouched.** The legacy MiniMax-driven generator keeps cycling (currently 0/N written; broken state preserved). The new daemon runs in parallel under a different `SERVICE_NAME` and different log file, so heartbeats and logs don't collide.
5. **No new model, no new API key, no new shim.** Goose uses the existing ladder_shim at 8796 → MiniMax-Text-01. Same budget envelope.
6. **No supervisord change shipped.** The new daemon is dormant until Robin explicitly adds it.

## Idempotency at every layer

- **Filename hashing** matches the legacy convention exactly: `gen_<md5_first_8>_<task[:35]>.json`. If the architect proposes the same task twice, the second write hits the existing-file guard and returns `{"status": "duplicate"}` without overwriting.
- **Validator equivalence** — `directive_mcp._validate()` mirrors `sentinel_directive_generator.validate_directive()` and (when importable) reads the live `ALREADY_BUILT` and `PROTECTED_FILES` sets from the legacy module. Fallback hardcoded lists are intentionally stricter (fail-closed) when import fails.
- **Quality-gate breaker** — `_validate()` calls `gate_quality_state.may_rebuild()` and rejects with the same "manual reset required" semantics the legacy generator uses. Architect is instructed to use `propose_breaker_action()` instead of trying to rebuild quarantined files.
- **Proposed-depth cap** (`MAX_PROPOSED = 40` by default, env-tunable) — daemon skips its cycle if the proposed/ backlog is already large enough. Prevents runaway proposal flood.
- **Goose subprocess timeout** (`GOOSE_TIMEOUT = 300s` default) prevents a stuck Goose from hanging the daemon forever.

## Rollout sequence (suggested)

### Step 1 — Land the files (no behaviour change)

Path A (preferred, per project rule "go through git PR"):
```bash
# On tower
cd D:\zo\zo-sentinel\zo-sentinel
git checkout -b feature/phase-0b-directive-architect
# Copy the three files from D:\zo\Zocomputer Agents\phase_0b\ into the repo
git add goose_recipes/directive_architect.yaml \
        zo_sentinel/mcp_servers/directive_mcp.py \
        zo_sentinel/sentinel_directive_generator_goose.py \
        ROLLOUT.md
git commit -m "Phase 0b: Goose-driven directive architect (additive, dormant)"
git push -u origin feature/phase-0b-directive-architect
# Open PR; review; merge to main
# Then bundle script picks them up on next bundle_run, Push-ZoSentinel.ps1 → GH
```

Path B (if bundle pipeline can ingest from outputs directly):
```bash
# On ZoComputer (after sync from outputs/):
# Files appear in /home/workspace/zo_sentinel/ at correct paths.
# Verify presence; no daemons read these files yet.
ls -la /home/workspace/zo_sentinel/goose_recipes/directive_architect.yaml
ls -la /home/workspace/zo_sentinel/mcp_servers/directive_mcp.py
ls -la /home/workspace/zo_sentinel/sentinel_directive_generator_goose.py
```

After Step 1, nothing observable has changed. Goose-Architect runs as before. Legacy generator runs as before.

### Step 2 — Smoke-test the recipe manually (one-shot, dormant daemon)

```bash
cd /home/workspace/zo_sentinel
export GOOSE_PROVIDER=openai
export GOOSE_MODEL=MiniMax-Text-01
export OPENAI_BASE_URL=http://127.0.0.1:8796/v1
export OPENAI_API_KEY=dummy_key_for_shim

CTX='{"schema":{},"layer1":{"product_spec":"smoke test"},"recent_failures":[],"proposed_depth":0}'
goose run --recipe goose_recipes/directive_architect.yaml \
          --params "context_json=$CTX"

# Expected: Goose calls read_gate_quality_state, read_already_built,
# read_protected_files, read_pending_directives, then maybe 1-6
# propose_directive / propose_breaker_action calls.
# Inspect: ls directives/proposed/
# Inspect: tail -50 /home/workspace/logs/directive_mcp.log
```

If anything looks wrong: `rm directives/proposed/*` and nothing downstream is affected.

### Step 3 — Add the daemon to supervisord (when smoke passes)

Add to `/etc/zo/supervisord-user.conf`:

```ini
[program:directive_generator_goose]
command=python3 /home/workspace/zo_sentinel/sentinel_directive_generator_goose.py
directory=/home/workspace/zo_sentinel
autostart=true
autorestart=true
stdout_logfile=/home/workspace/logs/directive_generator_goose.log
stderr_logfile=/home/workspace/logs/directive_generator_goose.log
stdout_logfile_maxbytes=10MB
stdout_logfile_backups=3
environment=DGG_POLL_SECS="600",DGG_MAX_PROPOSED_DEPTH="40"
```

```bash
supervisorctl -c /etc/zo/supervisord-user.conf reread
supervisorctl -c /etc/zo/supervisord-user.conf update
supervisorctl -c /etc/zo/supervisord-user.conf status directive_generator_goose
```

### Step 4 — Promotion workflow (proposed → pending)

The new daemon writes to `directives/proposed/`. goose_runner doesn't watch that dir. Two promotion options:

**Option 4a — manual one-shot:**
```bash
# Review proposed
ls directives/proposed/
cat directives/proposed/gen_*.json
# Move the ones you want built into pending
mv directives/proposed/gen_<id>.json directives/pending/
# goose_runner picks them up on next cycle
```

**Option 4b — auto-promote with a guard:**
A tiny separate script (out of scope of this PR) can auto-move proposed → pending after N minutes if the directive validates AND has not been flagged. Defer building this until 4a has proven the proposals are good.

## Rollback

Any of these are independently safe rollback moves:

- **Disable the new daemon:** `supervisorctl stop directive_generator_goose` + remove the supervisord block. Nothing else is affected.
- **Empty proposed/:** `rm directives/proposed/*.json`. Nothing has been promoted yet by definition; safe to wipe.
- **Remove the files:** `rm goose_recipes/directive_architect.yaml zo_sentinel/mcp_servers/directive_mcp.py zo_sentinel/sentinel_directive_generator_goose.py`. Restores pre-Phase-0b state.

The legacy generator and Goose-Architect are unaffected by any rollback step.

## Open question for Robin

- What's the **promotion cadence** preference for `proposed → pending`? Manual until proven (4a) is safer; auto with a TTL guard (4b) is faster. Recommend 4a for the first ~24h of cycles.

## Smoke-test checklist before declaring success

- [ ] `directive_mcp.log` shows tool calls from a goose invocation
- [ ] `directives/proposed/` contains 1+ new JSONs after a smoke run
- [ ] Each proposed JSON has the legacy-compatible filename pattern `gen_<md5_8>_<task_prefix>.json`
- [ ] Each proposed JSON validates manually against `sentinel_directive_generator.validate_directive`
- [ ] `goose_runner.log` is unaffected (no new directives processed automatically)
- [ ] `sentinel_directive_generator.log` continues its existing pattern unchanged
- [ ] No NEW errors in `write_service.log`
- [ ] At least one `propose_breaker_action` call appears in `directive_mcp.log` (proves the architect is using the quarantine-aware path)
