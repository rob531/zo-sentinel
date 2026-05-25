# Morning Review: Sentinel Patch C — Full Context

**Status:** A complete (`DB_SCHEMA.md` refreshed). C.1 staged (`patch_trust_synthesiser_pivot.sh` in DRY_RUN mode). C.2 and C.3 designed but not written.

---

## The story so far

You asked last night "what other code would be affected?" and the honest answer was: more than I'd first said. A full audit identified these writers of the two patched tables (`mcp_signal_scores`, `mcp_threat_associations`):

| File | Writes | State after patch_missing_pk_constraints |
|---|---|---|
| `signal_analyser.py` | signal_scores | ✅ Works. UNIQUE(server_id, signal_name) matches its dedup logic. |
| `pi_scorer.py` | signal_scores (`signal_name='injection_resilience'`) | ✅ Works. One row per server per signal matches constraint exactly. |
| `threat_intel_ingestor.py` | threat_associations | ⚠ Writes NULL source — same row will dedup inconsistently. Also has `:8773` port bug, double-slash URL bug, broken pidfile. |
| `rug_pull_monitor.py` | threat_associations | ⚠ Writes NULL source. Also has race-prone `SELECT MAX(id)+1` id generator. |
| `trust_synthesiser.py` | Reads signal_scores, writes to registry | ❌ **Still broken.** Its SELECT expects wide columns (`domain_trust`, etc.) which never existed. Every cycle falls back to hardcoded 74.0/TRUSTED_RESEARCH. This is why every assessed UI card looks identical. |

---

## C.1 — trust_synthesiser pivot (biggest win)

**File:** `/home/workspace/zo_sentinel/patch_trust_synthesiser_pivot.sh`
**Status:** Staged, DRY_RUN default. `APPLY=1` to execute.

**What it does:** rewrites one SQL block (~12 lines) to pivot the long-format table into wide format using `MAX(CASE WHEN signal_name='x' THEN score END)`. Everything downstream (`record.get('domain_trust')`) continues to work because the Python-dict row shape is identical after the pivot.

**Pre-conditions the script enforces before applying:**
1. `mcp_signal_scores` must have ≥10 rows (so signal_analyser is actually producing data)
2. `write_service:8772` is healthy
3. `trust_synthesiser.py` currently parses cleanly

**Expected result:** verdict distribution broadens from "74.0/TRUSTED_RESEARCH across the board" to a real distribution matching actual signal data. Every UI card starts showing differentiated trust scores.

**Rollback:** backup at `.bak.<timestamp>`; one-line `cp` restores.

---

## C.2 — threat_intel_ingestor fixes (not yet written)

**Target file:** `/home/workspace/zo_sentinel/threat_intel_ingestor.py`

**Four bugs to fix in one patcher:**

1. **Port `:8773` → `:8772`** on line `EXECUTE_URL = 'http://127.0.0.1:8773/execute'`. Same bug as attestation_engine had. Its current SELECTs fail silently returning 500.

2. **Double-slash URL in `ws_write()`**. Same construction as attestation_engine:
   ```python
   url = f'{WRITE_SERVICE_URL}/write'    # WRITE_SERVICE_URL already ends in /write
   ```
   Every write goes to `/write/write` and 404s.

3. **`/var/run/zo/` pidfile → `fcntl.flock(/tmp/threat_intel_ingestor.lock)`**. Same pattern we've used for all other daemons now.

4. **Add `source` field to every write.** Two call sites:
   - `process_world_articles()` writes threat_type='news_signal' — should set `source='world_articles'`
   - `fetch_osv_vulnerabilities()` writes threat_type='vulnerability' — should set `source='osv'`

**Why a new patcher and not re-use patch_attestation_engine.sh:**
The first three fixes are essentially copy-paste from that patcher. The fourth (adding `source` fields) is specific to this daemon's two write sites. Cleanest to stage it as its own file so each fix is reviewable independently.

**Shape:**
```bash
# patch_threat_intel_ingestor.sh
# 1. Port fix (sed)
# 2. Double-slash URL fix (ast-guarded regex over ws_write body)
# 3. flock replacement (ast-guarded regex over check_single_instance)
# 4. Two targeted str_replace calls to add 'source': '<value>' inside
#    each ws_write() call in process_world_articles and fetch_osv_vulnerabilities
# 5. Kill + setsid restart
# 6. 30s wait + verify mcp_threat_associations row count
```

**Risk:** low-to-medium. The daemon's state-touching is entirely in the two write blocks. Each is AST-validated after edit. Rollback via `.bak.<timestamp>` as usual.

**Estimated run time to actually write the patcher:** 20 min careful drafting, 5 min review, 5 min staging to disk.

---

## C.3 — rug_pull_monitor fixes (not yet written)

**Target file:** `/home/workspace/zo_sentinel/rug_pull_monitor.py`

**Three bugs:**

1. **Add `source='rug_pull_monitor'`** to every threat write (3 call sites in `report_threat()` and its callers).

2. **Race-prone id generator.** Current code:
   ```python
   id_result = ws_query("SELECT COALESCE(MAX(id), 0) + 1 FROM mcp_threat_associations")
   new_id = id_result.get('data', [[1]])[0][0]
   ws_write('mcp_threat_associations', {'id': new_id, ...})
   ```
   This is a TOCTOU race. If two cycles run simultaneously they could compute the same new_id and one would hit the PK constraint. Unlikely today (single daemon, sequential cycles) but not robust. Replace with hash-based id like signal_analyser does:
   ```python
   import hashlib
   id_key = f"{server_id}:{threat_type}:{evidence}"
   new_id = int(hashlib.md5(id_key.encode()).hexdigest()[:8], 16) % (2**31)
   ```
   Same approach signal_analyser uses for its `mcp_signal_scores` writes. The hash-derived id also gives natural idempotency — same threat report computes the same id, so a retry won't duplicate.

3. **Port and URL checks.** I didn't see these bugs in my quick read of rug_pull_monitor earlier. The fix-patcher should check for them rather than assume; if present, apply; if absent, skip cleanly.

**Risk:** low. Adding a field is non-destructive. The id-generator change makes writes safer, not riskier.

**Estimated time:** 15 min draft, 5 min review.

---

## Recommended morning order

1. Check `mcp_signal_scores` row count. If ≥10, it means `patch_missing_pk_constraints` did the job and signal_analyser is happy. If still 0, something else is wrong with signal_analyser — debug that first.

2. Run `APPLY=1 bash patch_trust_synthesiser_pivot.sh` **(C.1)**. Watch the verdict distribution query at the end of its output. Healthy result: a spread of verdicts with varying avg_score per tier. Unhealthy result: still all TRUSTED_RESEARCH 74.0 — means my pivot SQL has a bug I didn't catch.

3. Open the UI preview. Cards should now show differentiated trust scores. Signals sub-grid should render (ui_server.py already reads long-format correctly).

4. If all looks good, write and stage `patch_threat_intel_ingestor.sh` **(C.2)**. Run its DRY_RUN first, review, apply.

5. Then `patch_rug_pull_monitor.sh` **(C.3)**. Same pattern.

6. After both C.2 and C.3, Threat Feed in the UI should start populating as the next ingestor cycle completes. Risk Register should populate next cycle of risk_ranker (which reads threat_count via GROUP BY on mcp_threat_associations).

---

## What I did tonight while context was quiet

- **Refreshed `/home/workspace/zo_sentinel/DB_SCHEMA.md`** from live DB. Adds the new `source` column to mcp_threat_associations. Adds two prominent warnings: (a) that mcp_signal_scores is LONG-format and must not be SELECTed with wide-format column names, (b) that the new UNIQUE constraints require `source` to be populated.

- **Staged `patch_trust_synthesiser_pivot.sh`** with APPLY=1 required and a precondition check for minimum rows in mcp_signal_scores. The default dry-run shows the exact BEFORE and AFTER SQL for human review.

- **Wrote this doc** so you don't have to reconstruct the state from memory in the morning.

---

## What NOT to do

- Do not apply C.1 if `mcp_signal_scores` is empty. The script checks for this, but the instinct to "just run it and see" could be worth resisting. An empty pivot produces INSUFFICIENT for every row, which would at least REPLACE the spurious 74.0 verdicts with something more honest — but you'd lose the display anchor that 74.0 currently provides. Better to wait until signal_analyser has populated real rows.

- Do not batch C.1 + C.2 + C.3 into a single mega-patcher. Each has its own blast radius. If one goes wrong, debugging a three-in-one is much harder than a single-file patch.

- Do not touch `signal_analyser.py` itself. Its write logic was never the bug; the table constraint was. It's working now. Leave it alone.