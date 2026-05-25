# WEEKEND PLAN - 2026-04-18 (Saturday)

Weekend warrior schedule. Friday evening prep for Saturday work.

---

## NEW FINDING 2026-04-17 21:36 UTC

UI Risk Register tab was empty despite 200 rows in DB. Root cause: ui_server
query selected `last_assessed` column which doesn't exist on mcp_risk_register
(real column is `computed_at`). Fix already staged:

  python3 /home/workspace/zo_sentinel/fixes/fix_ui_risk_register_column.py

  pkill -9 -f 'python3 .*ui_server.py' 2>/dev/null
  sleep 2
  nohup python3 /home/workspace/zo_sentinel/ui_server.py \
      >> /home/workspace/logs/sentinel_ui_server.log 2>&1 &
  python3 /home/workspace/zo_sentinel/tests/rebaseline_protected_files.py ui_server.py

**Architectural gap revealed**: Gate 2 scans DAEMON source files for
stale_schema_ref, but doesn't scan the API file set (ui_server.py,
registry_api.py, approval_workflow.py, search_api.py, dashboard_api.py,
forensic_detail_api.py, comparison_api.py, advanced_filter_api.py,
manual_override_api.py, bulk_assess_api.py). Any of them could have similar
SELECTs drifting out of sync with live schema. Worth adding to Gate 2:

  - Copy the DAEMONS pattern into a new API_FILES list
  - Run same _check_payload_keys_match_columns + stale_schema_ref checks
  - Gate 2 runtime stays under 10s even with 10 more files

This is a Sunday or next-weekend task, not blocking.

---

## Tonight (done automatically, no action)

- **domain_trust_enrichment_v2 directive queued** at
  `/home/workspace/zo_sentinel/directives/gen_domain_trust_v2_build_domain_trust_enrichment_v2.json`
- Builder poll every 5 min, next cycle ~20:31 UTC 2026-04-17
- If successful: `domain_trust_enrichment_v2.py` appears in `/home/workspace/zo_sentinel/`
- If builder fails it: check `/home/workspace/logs/zo_sentinel_builder.log` tomorrow
  for stack trace. Directive file will be moved to `.done.` or stay as-is.


## Saturday morning (or whenever you're up) -- Stage 3 Integration

### Step 0: check what happened overnight

```bash
# Did v2 build?
ls -la /home/workspace/zo_sentinel/domain_trust_enrichment_v2.py 2>/dev/null

# If yes, harness-verify it
python3 /home/workspace/zo_sentinel/enrichment_harness.py \
    --enrichment /home/workspace/zo_sentinel/domain_trust_enrichment_v2.py \
    --runs 3 --sample-size 20 \
    > /home/workspace/logs/domain_trust_v2_harness.txt 2>&1

# Read verdict
bash /home/workspace/zo_sentinel/run_enrichment_evidence.sh
```

Expected outcomes:
- v2 verdict CANDIDATE: adopt it, retire v1 at your leisure
- v2 verdict REJECT: discard, stick with v1 until you can hand-craft a replacement
- v2 never built: rewrite directive with a sharper failure analysis, re-queue


### Step 1: pre-snapshot verdict distribution (CRITICAL)

Current verdict distribution (captured 2026-04-17 evening):

  NULL                   390
  TRUSTED_RESEARCH       373
  ENTERPRISE_CONTROLLED    2
  Total                  765

If TRUSTED_RESEARCH count drops by more than 75 servers (20%) after Stage 3,
STOP AND INVESTIGATE. Roll back from .bak files.


### Step 2: dry-run the Stage 3 patcher

```bash
python3 /home/workspace/zo_sentinel/fixes/stage3_enrichment_integration.py --dry-run
```

Review the output. It will show:
- 4 changes to trust_synthesiser.py (WEIGHTS, query, signals dict, INSUFFICIENT)
- 3 changes to gate_5 (WEIGHTS mirror, expected composite, tolerance)

If any line says `[WARN] doesn't match expected form`, the code has drifted
since Friday. Read the patcher's constants, update, try again.


### Step 3: apply + restart

```bash
python3 /home/workspace/zo_sentinel/fixes/stage3_enrichment_integration.py --restart
```

This runs the patches, rebaselines protected files automatically, and
restarts trust_synthesiser in one command.


### Step 4: run gates to verify no regression

```bash
python3 /home/workspace/zo_sentinel/tests/gates/run_gates.py \
    > /home/workspace/logs/gate_results.txt 2>&1
```

Expected: all 100+ checks pass including the new Gate 5 composite check
against expected value 67.41 (enrichments-missing canary path).


### Step 5: wait 30 min, check verdict distribution

```bash
# After one trust_synthesiser cycle (CYCLE_INTERVAL=1800s=30min):
```

Query via write_service or MCP tool:
```sql
SELECT COALESCE(verdict, 'NULL') AS verdict, COUNT(*) AS n
FROM mcp_server_registry GROUP BY verdict ORDER BY n DESC
```

Compare to pre-snapshot above.

Acceptable shifts:
- TRUSTED_RESEARCH count +/- 10% (server_ids crossing threshold normal)
- A few TRUSTED_RESEARCH -> ENTERPRISE_CONTROLLED transitions (normal at boundary)
- INSUFFICIENT count may rise if enrichment coverage is low

Cause for rollback:
- TRUSTED_RESEARCH drops below 250 (>30% collapse)
- Everything becomes KNOWN_THREAT (unequivocal bug)
- Gates fire NEW errors (not pre-existing ones)


## Rollback procedure

If Step 5 shows bad distribution shift:

```bash
# Find the most recent .bak files from Saturday's patch
ls -t /home/workspace/zo_sentinel/trust_synthesiser.py.bak.* | head -1
ls -t /home/workspace/zo_sentinel/tests/gates/gate_5_synthesis_flow.py.bak.* | head -1

# Restore (substitute actual bak filenames)
cp /home/workspace/zo_sentinel/trust_synthesiser.py.bak.YYYYMMDD_HHMMSS \
   /home/workspace/zo_sentinel/trust_synthesiser.py
cp /home/workspace/zo_sentinel/tests/gates/gate_5_synthesis_flow.py.bak.YYYYMMDD_HHMMSS \
   /home/workspace/zo_sentinel/tests/gates/gate_5_synthesis_flow.py

# Restart
pkill -9 -f 'python3 .*trust_synthesiser.py' 2>/dev/null
rm -f /tmp/trust_synthesiser.lock
sleep 2
nohup python3 /home/workspace/zo_sentinel/trust_synthesiser.py \
    >> /home/workspace/logs/sentinel_trust_synthesiser.log 2>&1 &

# Rebaseline to acknowledge the rollback
python3 /home/workspace/zo_sentinel/tests/rebaseline_protected_files.py \
    trust_synthesiser.py
```


## Other weekend touchpoints (optional, if energy/time)

- Install gate cron if not done: `bash /home/workspace/zo_sentinel/install_gate_cron.sh`
- Check if cron is firing at 00:00 UTC: `ls /home/workspace/logs/gate_runs/`
- risk_ranker only processed 200 of 765 servers -- worth investigating its
  query LIMIT if ambitious. File: `/home/workspace/zo_sentinel/risk_ranker.py`,
  look for `LIMIT` in get_servers().
- **Gate 2 extension to cover API file set** (see "NEW FINDING" at top)

---

Written Friday 2026-04-17 ~20:30 UTC for Saturday 2026-04-18 work.
All fix scripts pre-staged under `/home/workspace/zo_sentinel/fixes/`.