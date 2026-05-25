# ZO-Sentinel Monday Checklist — 2026-04-20

**Written:** Sunday 2026-04-19 23:30 UTC after long commit-A + roadmap-v2 session.
**Updated:** 2026-04-20 00:25 UTC with container-hibernation finding.

**Purpose:** Sequenced Monday-morning steps with expected outputs and decision branches so you don't have to reconstruct context first thing.

Estimated time for full walkthrough: **30 minutes** if everything worked overnight.

---

## ⚠️ IMPORTANT: Container hibernation confirmed (late Sunday finding)

Evidence from 14 days of mesh_memory + build_complete event timestamps shows ZoComputer containers **DO hibernate during idle periods**. Every day in the historical record shows clumped activity windows matching your work sessions, with dead zones of 8-20+ hours in between. Example: April 14 shows activity 15:26-23:28 UTC, then nothing until April 16.

**What this means for Monday morning:**

* The three directives queued overnight (`001_*`, `002_*`, `003_*`) **will NOT have been processed overnight**. Container was probably asleep.
* The accelerated fetcher **did not continue warming overnight**. Cache is still wherever it was when container went dormant (expect ~700-750/803 from tonight's progress).
* When you log in Monday morning, ZoComputer cold-starts the container and your first interaction triggers wakeup. Daemons relaunch via `go.sh` cron (if it re-fires) or need manual `zm go`.
* **Directives will process within 5-10 minutes of you being interactively online Monday morning.**

**Implication for the checklist:** Step 0 (`zm go`) is essential, not optional. Run it first thing.

**Strategic implication (not tonight's work):** legitimate keepalive = register critical daemons as ZoComputer scheduled tasks. ZoComputer wakes containers to run scheduled tasks; it doesn't wake them for daemon_wrapper processes. File this as a Monday-afternoon design discussion, not an urgent fix.

---

## Step 0: Container health check (FIRST, 2 min)

ZoComputer runs on Modal underneath. Overnight hibernation is expected; container likely cold-started when you first interacted Monday morning. This check verifies recovery.

```bash
# Are core services alive?
curl -s http://127.0.0.1:8772/health
# Expect: {"status":"ok","version":"1.3.0",...}
# If connection refused -> container still cold, go to recovery below

# Is the builder running?
pgrep -f 'zo_sentinel_builder.py' && echo 'builder up' || echo 'builder DOWN'

# Is the fetcher running?
pgrep -f 'ecosystems_metadata_fetcher.py' && echo 'fetcher up' || echo 'fetcher DOWN'
```

**If any core service is DOWN** (expected after overnight hibernation):

```bash
# Full recovery: re-establish cron + relaunch all daemon_wrapper services
zm go

# Wait 60s then re-check
sleep 60
curl -s http://127.0.0.1:8772/health
pgrep -f 'zo_sentinel_builder.py'
pgrep -f 'ecosystems_metadata_fetcher.py'
```

`zm go` is the idempotent recovery command — per userMemories, `go.sh v1.3` auto-reinstalls the watchdog cron on every invocation and relaunches all wrapper-managed services. Running it when nothing is broken is safe; running it after hibernation is essential.

**Things that DO survive hibernation** (don't need recovery):

* DuckDB data files (all 700+ cached MCPs, all enrichments, all signal scores)
* All queued directives in `/home/workspace/zo_sentinel/directives/`
* All fixes patchers and generated modules
* All your session state from Sunday

**Things that DON'T survive** (need `zm go`):

* The watchdog cron that keeps services alive
* Running daemon processes (fetcher, bridge, builder, etc.)
* In-flight builder cycles (directive currently being generated may be half-written)

---

## Step 1: Did builder process the queued directives? (5 min)

After `zm go` fires and builder polls (within 5 min of waking), check:

```bash
tail -80 /home/workspace/logs/zo_sentinel_builder.log
```

**What to look for:**

* Lines for `directory_ingestor_anthropic_pilot`, `pilot_harness_framework`, `threat_feed_cache_refresher`
* Each should end with `--- [task_name]: OK ---` or `FAILED`

**Also check:**
```bash
ls /home/workspace/zo_sentinel/directives/ | grep -E '^(001|002|003)_'
```

* If all three show `.done.json` extension → builder finished them
* If any still show `.json` (no `.done`) → still in queue or builder stuck; wait another cycle

**Check generated files exist:**
```bash
ls -la /home/workspace/zo_sentinel/mcp_directory_ingestor.py \
       /home/workspace/zo_sentinel/pilot_harness.py \
       /home/workspace/zo_sentinel/threat_feed_cache.py
```

**Expected:** three files ~3-8KB each.

**If one is missing:** check the corresponding `.done.json` for FAILED status, check GENERATION_FAILURES.md for the reason. Most common causes: MiniMax returned empty, Ollama timeout. Manual re-queue by removing `.done.json` extension:

```bash
mv /home/workspace/zo_sentinel/directives/001_directory_ingestor_anthropic_pilot.done.json \
   /home/workspace/zo_sentinel/directives/001_directory_ingestor_anthropic_pilot.json
```

Builder will retry on next 5-min cycle.

---

## Step 2: Restore fetcher to 6h cycles (1 min)

Fetcher was running at 5-min warmup cycles when we went to bed. By Monday the cache should be ~95%+ warm (assuming container didn't hibernate mid-warmup; if it did, fetcher picks up where it left off).

```bash
# First confirm warmup is essentially done
curl -s http://127.0.0.1:8772/query -H 'Content-Type: application/json' \
  -d '{"sql":"SELECT COUNT(*) AS cached, (SELECT COUNT(*) FROM mcp_server_registry) AS total FROM mcp_ecosystems_metadata"}'
```

* If cached >= 95% of total → flip to normal
* If cached <95% → leave on 5min for another hour, re-check

When ready:

```bash
python3 /home/workspace/zo_sentinel/fixes/patch_fetcher_restore_normal_cycle.py

pkill -f 'daemon_wrapper.sh ecosystems_metadata_fetcher'
sleep 2
source /home/workspace/zo_mesh/.zo_env
nohup bash /home/workspace/zo_mesh/daemon_wrapper.sh \
    ecosystems_metadata_fetcher \
    /home/workspace/zo_sentinel/ecosystems_metadata_fetcher.py \
    >> /home/workspace/logs/ecosystems_metadata_fetcher.log 2>&1 &
```

---

## Step 3: Inspect generated directory_ingestor before running it (5 min)

The generated module is Python code produced by an LLM. Before running:

```bash
less /home/workspace/zo_sentinel/mcp_directory_ingestor.py
```

**Review checklist:**

* [ ] Has `if __name__ == '__main__':` block
* [ ] Calls `ingest_anthropic_reference()` not a daemon loop
* [ ] Uses `requests.post('http://127.0.0.1:8772/execute', ...)` NOT direct duckdb.connect
* [ ] CREATE TABLE statements for `mcp_directory_mentions` and `mcp_discovery_candidates`
* [ ] User-Agent header set to something identifying zo-sentinel
* [ ] Timeout 15s on fetch
* [ ] No hardcoded secrets or API keys

**If it looks good:** run it
**If it looks wrong:** either manually edit the file OR re-queue the directive with a more specific context hint

---

## Step 4: Run directory_ingestor pilot (2 min)

```bash
python3 /home/workspace/zo_sentinel/mcp_directory_ingestor.py
```

**Expected output:**

* Fetch of modelcontextprotocol.io/servers succeeds
* Parse finds >= 15 entries
* At least 10 match existing registry
* Exit code 0
* Log summary with counts

**Then query results:**

```bash
curl -s http://127.0.0.1:8772/query -H 'Content-Type: application/json' \
  -d '{"sql":"SELECT directory_name, COUNT(*) FROM mcp_directory_mentions GROUP BY 1"}'

curl -s http://127.0.0.1:8772/query -H 'Content-Type: application/json' \
  -d '{"sql":"SELECT candidate_name, candidate_url FROM mcp_discovery_candidates LIMIT 20"}'
```

**Expected:**

* `mcp_directory_mentions`: 10-30 rows for `anthropic_reference`
* `mcp_discovery_candidates`: 0-10 rows (MCPs Anthropic lists that aren't in our registry yet — valuable if > 0)

**If pilot fails:** debug the specific failure. Most common: page structure differs from what the LLM guessed. Re-queue directive with hint about actual page structure if needed.

---

## Step 5: Re-run ecosystems_enrichment_adapter against warm cache (3 min)

Sunday's first adapter run scored 50 temporal_stability values against partial data, producing only 2 distinct values (40 and 90). The fetcher is now much fuller (~700+ servers). Re-running the adapter will produce the full diversity signal.

```bash
python3 /home/workspace/zo_sentinel/ecosystems_enrichment_adapter.py --once
```

**Expected:**

* `total: 700+, community_written: 700+, temporal_written: 700+, write_failed: 0`
* Runs in ~30 seconds

**Wait 5 minutes for signal_bridge cycle**, then verify diversity improved:

```bash
curl -s http://127.0.0.1:8772/query -H 'Content-Type: application/json' \
  -d '{"sql":"SELECT signal_name, COUNT(DISTINCT score) AS distinct_vals, ROUND(MIN(score),1) AS lo, ROUND(MAX(score),1) AS hi FROM mcp_signal_scores GROUP BY signal_name ORDER BY distinct_vals DESC"}'
```

**Expected improvements:**

* `temporal_stability` distinct: **3 → 5-6** (was flat 50, then 40/50/90, should now span 40/45/65/80/90/95)
* `community_signal` distinct: 30 → 40-50
* `supply_chain` distinct: 34 → 40-50
* `domain_trust` distinct: 11 → 15-20

---

## Step 6: Run Gate 9 to see the full improvement story (1 min)

```bash
python3 /home/workspace/zo_sentinel/tests/gates/run_gates.py 9
```

**Expected:** 5 of 6 signals PASS. Only `temporal_stability` might still be borderline (depending on age data distribution).

If all 6 pass: Commit A is fully realized, signal layer has achieved discriminating quality across all scored MCPs.

---

## Step 7: Restart flaked sentinel daemons (3 min, optional)

These aged out overnight (heartbeat >30min):

* trust_synthesiser
* signal_analyser
* attestation_engine
* mcp_scanner
* risk_ranker
* threat_intel_ingestor

They're not critical for today's work but should be resurrected for downstream signal synthesis. Rerun the resurrection script:

```bash
bash /home/workspace/zo_mesh/resurrect_sentinel_daemons.sh
```

Check heartbeats 5 minutes later:

```bash
curl -s http://127.0.0.1:8772/query -H 'Content-Type: application/json' \
  -d '{"sql":"SELECT service, CAST(EXTRACT(EPOCH FROM (now() - last_heartbeat)) AS INTEGER) AS age_sec FROM service_health WHERE service IN (\"trust_synthesiser\", \"signal_analyser\", \"attestation_engine\", \"mcp_scanner\", \"risk_ranker\", \"threat_intel_ingestor\") ORDER BY age_sec ASC"}'
```

All should show age < 300s. If `threat_intel_ingestor` still aged out: stale pidfile issue from earlier today, fix:

```bash
rm -f /var/run/zo/threat_intel_ingestor.pid
pkill -f 'daemon_wrapper.sh threat_intel_ingestor'
sleep 2
source /home/workspace/zo_mesh/.zo_env
nohup bash /home/workspace/zo_mesh/daemon_wrapper.sh \
    threat_intel_ingestor \
    /home/workspace/zo_sentinel/threat_intel_ingestor.py \
    >> /home/workspace/logs/threat_intel_ingestor.log 2>&1 &
```

---

## Step 8: Investigate write_queue_log / heartbeat issue (when you have time)

write_service.write_queue_log stopped writing at 21:46 UTC on Sunday, and service_health heartbeat for write_service is now 2+ hours stale. The service itself is still functioning (writes to other tables succeed), but these internal-accounting paths are broken.

**Suspected cause:** DuckDB internal assertion error from Sunday evening corrupted one of the prepared-statement paths but not the main write path.

**Investigation steps:**

```bash
tail -200 /home/workspace/logs/write_service.log | grep -E '(assertion|INTERNAL|Error)' | head -20
```

If DuckDB assertion errors keep appearing, consider:

1. `CHECKPOINT` manually via:
   ```bash
   curl -X POST http://127.0.0.1:8772/execute \
     -H 'Content-Type: application/json' \
     -d '{"sql":"CHECKPOINT", "wait":true}'
   ```

2. If that doesn't clear, full write_service restart (use commit 3 self-kill path rather than pkill):
   ```bash
   curl -X POST http://127.0.0.1:8772/execute \
     -H 'Content-Type: application/json' \
     -d '{"sql":"VACUUM", "wait":true}'
   ```

Not urgent. Write path functional. Heartbeat staleness is cosmetic from a functionality standpoint but does make monitoring misleading.

---

## Reference: infrastructure stack

ZoComputer runs on Modal (serverless containers) + Neon (Postgres) + Upstash (Redis) + Cloudflare (DNS/tunnels). This means:

* **Container hibernation IS a thing** — confirmed by historical activity gap analysis. Not just theoretical.
* DuckDB data files persist across hibernation
* Bootstrap via cron doesn't survive hibernation — `zm go` is the recovery command
* Your flat-fee plan means no compute billing exposure from your side, but caffeine-style keepalives may violate ZoComputer ToS and are not recommended
* Legitimate keepalive = register critical daemons as ZoComputer native scheduled tasks (to be designed Monday afternoon)

## Reference: tonight's completed work

* Commit 3 resilience (daemon_wrapper, liveness_probe, write_service self-kill)
* Commit 4 signal quality (signal_bridge w/ computed_at fix, Gate 8 relax, Gate 9 diversity)
* Commit A (ecosyste.ms fetcher + adapter + 3 bug fixes)
* Fetcher URL-routing fix (npm dark matter now visible)
* Sentinel scoring daemons resurrected after 36h outage
* Threat dedup + canonicalization for bootstrap IDs
* SENTINEL_ROADMAP_v2.md + addendum written
* 3 builder-native directives queued (directory_ingestor, pilot_harness, threat_feed_cache)
* 4 design directives written as reference docs (not for builder)
* Container hibernation pattern confirmed via log analysis

## Reference: key files touched

* `/home/workspace/zo_sentinel/ecosystems_metadata_fetcher.py` — v3 with URL routing
* `/home/workspace/zo_sentinel/ecosystems_enrichment_adapter.py` — v2 with run_id
* `/home/workspace/zo_sentinel/signal_bridge.py` — v2 with computed_at fix
* `/home/workspace/zo_sentinel/SENTINEL_ROADMAP_v2.md`
* `/home/workspace/zo_sentinel/SENTINEL_ROADMAP_v2_addendum.md`
* `/home/workspace/zo_sentinel/directives/001_*.json` through `003_*.json`
* `/home/workspace/zo_sentinel/fixes/patch_fetcher_restore_normal_cycle.py`
* `/home/workspace/zo_sentinel/fixes/patch_fetcher_url_routing.py`
* `/home/workspace/zo_sentinel/fixes/patch_enrichment_pipeline_schema_fixes.py`
* `/home/workspace/zo_sentinel/fixes/patch_ecosystems_fetcher_write_path.py`

## Reference: bridge investigation resolved

The "temporal_stability bridge mystery" from earlier tonight turned out to be a misread, not a bug. Bridge correctly wrote all 50 overrides at 22:14 UTC with evidence prefix `[bridge:temporal_stability_enrichment]`. The apparent flat distribution was because 749 legacy signal_analyser rows at 50.0 swamped the 50 bridge-written rows statistically. Adapter re-run against warm cache (Step 5) will flip the ratio. No code fix required.

---

## If everything works, what's the Monday win?

1. **Directory ingestor generates and runs** → Anthropic reference list mentions now in DB
2. **Adapter re-run against warm cache** → temporal_stability, community_signal, domain_trust diversity all climb
3. **Gate 9 passes 5-6 of 6 signals** → signal layer declared production-quality
4. **Pilot harness + threat_feed_cache generated** → foundation for Phase IV.a threat intel ready for next session

Total new work product: **3 generated modules, measurably better signal diversity, directory coverage for Anthropic reference list, and the scaffolding for threat intel integration**. Enough to call Monday productive without requiring extended focus time.

---

## Escalation if things go wrong

* Builder not processing directives → check `ollama list` and `curl http://127.0.0.1:11434` for Ollama health
* Generated code crashes on run → manually edit, or re-queue with stricter context hint
* DuckDB assertion errors increase → consider scheduled VACUUM + full restart via commit 3 self-kill path
* MiniMax API returns 402 credit errors → use llama3.2:3b fallback only for this cycle; check MiniMax dashboard
* **Container still cold after `zm go`** → wait another 60s, retry. If 5+ minutes pass and services still down, check Modal status page at status.modal.com. This would be unusual.

**Don't panic on any single failure.** Sunday's work proved the infrastructure is resilient — directives can be re-queued, services respawn via daemon_wrapper, Gate 8 quarantines bad enrichments. The system tolerates individual component failures. Overnight hibernation is expected and `zm go` handles recovery idempotently.