# Diagnose Importlib Smoke Failures — 2026-05-24

## Conclusion

**Root Cause: smoke_test.py has STALE interface expectations, NOT actual import failures.**

All modules import cleanly via importlib. The failures are false positives from
a test whose expectations were never updated as modules evolved.

---

## Evidence

### 1. importlib Direct Import — ALL OK (11/11)

```
OK: signal_analyser (49 attrs)
OK: attestation_engine (37 attrs)
OK: search_api (29 attrs)
OK: lookup (27 attrs)
OK: threat_intel_ingestor (42 attrs)
OK: risk_ranker (40 attrs)
OK: registry_api (21 attrs)
OK: mcp_scanner (35 attrs)
OK: trust_synthesiser (38 attrs)
OK: known_threats (10 attrs)
OK: rug_pull_monitor (30 attrs)
```

### 2. gate_8_new_module.py — 64/68 passed (94%)

```
[FAIL] temporal_stability_enrichment.py exists [built_file_missing] (known)
[FAIL] permission_scope_enrichment.py exists [built_file_missing] (known)
[FAIL] tool_description_safety_enrichment_v2.py exists [built_file_missing] (known)
[FAIL] temporal_stability_enrichment_v2.py exists [built_file_missing] (known)
```

All 4 failures are **files deleted after build** — marked as (known) in gate output.
No quarantine triggered. Breaker not tripped.

### 3. smoke_test.py — 4 stale expectations

| Module | Expected (wrong) | Actual (correct) |
|--------|-----------------|-----------------|
| attestation_engine | `create_all` | `create_attestations_table` |
| signal_analyser | `Sentinel` class + `main()` | daemon with `run()`, no class |
| search_api | `main()` | FastAPI app with `run()` |
| lookup | `get_write_url`, `get_director_maturity` | CLI with no WS URL helpers |

---

## No Actual Importlib Failures

- No `ModuleNotFoundError` on core modules
- No syntax errors in source
- No `__pycache__` corruption
- No Python version mismatches
- No missing dependencies

---

## Recommendation

**Option A (preferred):** Retire `smoke_test.py` — it tests against fixed interface
expectations that drift over time. Gate 8's contract-based approach is correct.

**Option B:** Align `smoke_test.py` with current module shapes, removing assertions
for `Sentinel`, `main` (signal_analyser), `create_all` (attestation_engine), etc.

No DB writes, no quarantine, no corrective action needed on modules themselves.
