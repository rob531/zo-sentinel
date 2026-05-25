# Enrichment Staging — Design

Date: 2026-04-17
Status: design complete, scaffolding staged, execution deferred to weekend

## Problem

`signal_analyser.py` produces 6 signals for every MCP. Of the 6, 5 return
a single distinct value across all scored servers:

| signal                   | distinct_vals | range       | verdict           |
|--------------------------|---------------|-------------|-------------------|
| permission_scope         | 2             | 65.0 – 100  | weak discrimination |
| community_signal         | 1             | 80.0 flat   | BAD                 |
| domain_trust             | 1             | 80.0 flat   | BAD                 |
| supply_chain             | 1             | 0.0 flat    | BAD (never computed)|
| temporal_stability       | 1             | 50.0 flat   | BAD (default only)  |
| tool_description_safety  | 1             | 100.0 flat  | BAD                 |

**A signal is only useful if it discriminates between servers.** The
heuristic: *bad signal = same signal across all inputs.*

Because 5/6 signals are flat, the composite trust_score is near-identical
for every MCP, and every verdict lands in `TRUSTED_RESEARCH (65-74)`. The
pipeline is healthy; the scoring logic is anaemic.

## Goal

Improve signal sophistication without regenerating any file that has been
hand-patched or passed smoke tests. Specifically: no changes to
`signal_analyser.py`, `trust_synthesiser.py`, `full_schema_bootstrap.py`,
the UI stack, or any file listed in `PROTECTED_FILES`.

## Approach: staged evidence-gated enrichment

Four stages, with a human review gate between stage 2 and stage 3.

### Stage 0 — scaffolding (one-time, hand-written, this weekend)

Three files, all in `/home/workspace/zo_sentinel/`:

1. **`enrichment_schema_bootstrap.py`** — creates `mcp_signal_enrichments`
   table in main DuckDB. Runs via `python3` after `full_schema_bootstrap.py`
   on every boot; idempotent.

2. **`enrichment_harness.py`** — generic runner. Takes a path to an
   enrichment module, generates N synthetic MCP inputs with varied
   metadata, calls the module's `compute_score(metadata)` N times, writes
   each result to `mcp_signal_enrichments` with run_id + input_fingerprint.

3. **`enrichment_evidence.sql`** — the gate query. Groups by
   `enrichment_name`, reports `distinct_vals`, `distinct_fingerprints`,
   `stddev`, and a per-enrichment verdict of
   `REJECT`/`WEAK`/`CANDIDATE for integration`.

4. **`patch_directive_generator_staged.sh`** — patcher for the directive
   generator. Updates the prompt so enrichment modules expose
   `compute_score(metadata) -> (score, evidence_dict)` as pure functions,
   do NOT write to DB, and do NOT touch protected files. POLL_SECS stays
   at 7200 until scaffolding is proven; reduced to 3600 by a later edit.

### Stage 1 — computation (generator-driven, after scaffolding lands)

Directive generator proposes enrichment modules. Each is a new file:
`<signal_name>_enrichment.py`. Each exposes:

```python
def compute_score(metadata: dict) -> tuple[float, dict]:
    """Pure function. Given MCP metadata, return (score_0_100, evidence_json).

    MUST be deterministic for a given input.
    MUST read multiple metadata fields so input variety produces output variety.
    MUST NOT write to DB directly -- the harness does that.
    MUST NOT import any protected module to mutate it.
    """
```

Directive generator has a clear example in its prompt showing exactly this
shape.

### Stage 2 — evidence (human-run, manual)

For each enrichment that's been generated:

```bash
python3 /home/workspace/zo_sentinel/enrichment_harness.py \
    --enrichment /home/workspace/zo_sentinel/supply_chain_enrichment.py \
    --runs 3
```

Harness runs the enrichment against 3 different synthetic input sets,
writing to `mcp_signal_enrichments`. Then:

```bash
python3 -c "import duckdb; print(duckdb.connect('/home/workspace/zo_mesh/zomesh.db', read_only=True).execute(open('/home/workspace/zo_sentinel/enrichment_evidence.sql').read()).fetchdf())"
```

Or via `zo_db_query` inside a Claude session. Either way, the verdict column
tells you which enrichments have earned integration.

### Stage 3 — integration (human-approved, hand-written shims)

For each CANDIDATE enrichment, write a minimal shim that wires it into
the pipeline. Shims modify protected files — one-time, deliberate,
reviewed. Three specific shim patterns are expected:

- **verdict shim** → hand-edit `trust_synthesiser.py` to read enrichment
  scores and include them in the composite
- **schema shim** → add enrichment-reading columns to
  `mcp_server_registry` or a view; updates to `full_schema_bootstrap.py`
- **display shim** → new `enrichment_view_api.py` (NEW file, not a
  modification) that serves enriched scores to UI; UI files unchanged
  until a UI refresh is warranted

After shim applied to a protected file, the file is re-added to
`PROTECTED_FILES` to prevent regeneration.

## Loop risks addressed

| Risk | Mitigation |
|---|---|
| Enrichment outputs never reach verdict | Stage 3 shim explicitly targets trust_synthesiser |
| Schema for enrichment table gets wiped on reboot | `enrichment_schema_bootstrap.py` is a peer to `full_schema_bootstrap.py`, runs on every boot, idempotent |
| Enrichment is fake (hash-based, deterministic but not input-sensitive) | harness writes `input_fingerprint`; evidence query REJECTs enrichments where `distinct_fingerprints = 1` |
| Generator keeps proposing the same enrichments | each enrichment, once generated, is added to `ALREADY_BUILT` in the generator (normal flow) |
| Enrichment runs infinitely and burns tokens | manual invocation via harness CLI -- no daemon until scaffold is proven |

## What is NOT being built right now

- No automatic enrichment scheduling. Runs are manual via the harness.
- No Stage 3 shims. Those are weekend+ work, after Stage 2 evidence arrives.
- No UI changes. Existing UI remains untouched.
- No changes to signal_analyser.py or trust_synthesiser.py during Stage 1/2.

## Execution order (weekend)

1. Write `enrichment_schema_bootstrap.py`, run once to create the table.
2. Write `enrichment_harness.py`, test it against a dummy enrichment that
   returns `hash(server_id) % 100` -- should show as REJECT in evidence
   query (no fingerprint variety).
3. Write `enrichment_evidence.sql`, run it against the dummy -- confirms
   the gate query works.
4. Apply `patch_directive_generator_staged.sh` -- updates the prompt,
   restarts generator.
5. Wait one generator cycle. Review proposed enrichment modules.
6. Run harness on each. Run evidence query. Select CANDIDATEs.
7. For each CANDIDATE, hand-write a shim. This is the first time we
   touch a protected file -- deliberate, one module at a time.

## Success criteria

- `mcp_signal_enrichments` has at least one enrichment with:
  - `distinct_vals > 10` across 80+ MCPs
  - `distinct_fingerprints > 1` (sensitive to input variety)
  - Verdict `CANDIDATE for integration`
- After first integration shim lands, `mcp_server_registry.verdict`
  distribution spreads to at least 3 distinct verdicts.
- No regression: existing `trust_score` range and verdict set do not
  shrink (monotonic improvement).

## Notes

The original single-pass patcher (`patch_directive_generator_enrichment.sh`)
was staged on 2026-04-17 then superseded by this design the same day.
Kept as a refusal stub so an accidental re-run fails clean.