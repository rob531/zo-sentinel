# PLAN_200K — Scaling zo-sentinel from 80K to 200K assessed MCPs

**Status:** PROPOSED (chairman directive 2026-07-15) · **Owner:** Claude (CEO) · **Eval:** see §9 (PASS w/ conditions)

## 1. Goal

200,000 MCP servers **assessed with defensible signals** — not 200K rows. A server counts toward the goal only if it has: (a) provenance-stamped source, (b) verified identity (ukey=sid), (c) student-model scores across all 7 axes OR an explicit fail-visible UNKNOWN, and (d) passes the fabricated=0 audit. Rows failing the evidence bar stay `tier=catalogued` and do not count.

## 2. Baseline (measured 2026-07-15, prod /freshness)

| Metric | Value |
|---|---|
| registry_rows | 80,539 |
| scored_servers | 66,565 |
| never_scored (by design) | 13,974 |
| scores_rows | 465,955 |
| Gap to goal (rows) | +119,461 |
| Gap to goal (scored) | +133,435 |

## 3. Universe sizing (measured 2026-07-15 via source APIs)

| Source | Count | Method |
|---|---|---|
| npm `keywords:mcp` | 60,109 | registry.npmjs.org search API |
| GitHub `topic:mcp` | 50,181 | GitHub search API |
| GitHub `mcp-server in:name` | 47,734 | GitHub search API |
| GitHub `mcp in:name` (noisy tail) | 272,809 | GitHub search API |
| PyPI name contains `mcp` | 18,452 | pypi.org simple index (850,014 total) |
| Glama directory | ~22,775 | public listing (May 2026) |
| PulseMCP directory | ~22,290 | public listing |
| Official MCP Registry | ~9,652 latest records | registry API (May 2026) |

Overlap is heavy: directories mostly index the same GitHub repos, and our 80.5K already contains Glama's bulk import (~48.5K rows). The remaining growth must come from **package registries and GitHub direct**, not directories.

## 4. Lanes (phased)

**Phase A — Directory saturation (wk 1–2).** Existing paginators (Glama, PulseMCP, official registry, Smithery, mcp.so) run to exhaustion on the nightly ~3K rotation. Est. net-new after dedup: **+10–15K**. Cheap; already built.

**Phase B — GitHub direct lane (wk 2–6).** New crawler over `topic:mcp` ∪ `mcp-server in:name` (~60–70K distinct repos). Shard search queries by `created:` date windows to defeat the 1,000-result API cap. Authed token via AgentVault `github` (5K req/hr). Est. net-new: **+30–40K**.

**Phase C — Package-registry lanes (wk 3–8).** npm `keywords:mcp` (60K) + PyPI `mcp` (18.5K). Resolve package→repo; where repo exists, merge into repo identity; repo-less packages get package-level sid. Est. net-new: **+30–45K** (npm-only long tail is large).

**Phase D — Gated long tail (wk 6–10, reserve tank).** GitHub `mcp in:name` (272K, junk-heavy). Admit only on MCP fingerprint: MCP SDK dependency, server entrypoint, or tools/manifest schema present. Precision target ≥90% on a 500-repo hand-audited sample **before** the lane opens. Est. net-new: **+30–60K**, opened only as far as needed to close the gap.

Projected total: 80.5K + 100–160K → **180K–240K**. 200K is P50 by mid-October; Phase D precision is the swing variable.

## 5. Identity & dedup invariants (non-negotiable)

- **ukey = sid**, never URL. Repo URL ≠ server identity (lesson: 14K rows stamped with a sibling's tier, PR #1471). The deleted URL tier-propagation stays deleted; its invariant check extends to all new lanes.
- **unknown ≠ zero** (lesson: Glama fabricated tool_count on 48,544 rows, #1278). Empty API fields map to NULL, never 0. Every new lane ships with a fabrication audit query.
- **Provenance-first, fail-closed**: every row carries `registry_source` + fetch timestamp + raw-payload hash. No source, no row.
- Pre-flight tests for each lane use **real corpus variance** (variable-length labels, heterogeneous metadata), not synthetic uniform inputs.

## 5b. Canonicalization & derived-repo detection (build on prior art, don't reinvent)

The dedup machinery for lanes B–D already exists in-repo — `canonicalizer.py` (Commit B), `mcp_project_canonicalizer.py`, `fixes/pilot_canonicalization.py` (+results), `deduplicator.py`. April 2026 pilot verdict: **GO, 100% cross-registry bridge rate** on sample via ecosyste.ms.

**Existing mechanisms to reuse:**

- **Canonical keys**: `repo:<host>/<owner>/<name>` and purl `pkg:<ecosystem>/<name>`; `canonical_id` = sha256 of sorted identifier set. Tables: `mcp_project_canonical`, `mcp_project_members`, `canonical_drift_log`, `_uncertain`.
- **ecosyste.ms lookup** (`packages.ecosyste.ms/api/v1/packages/lookup`): repo URL → "cousins" across npm/PyPI/Go. This is the Phase C package→repo bridge, already validated. It directly surfaces republished derivatives (pilot found `@iflow-mcp/chrome-devtools-mcp`, `chrome-devtools-mcp-customized`, `@skeksk91/...` all descending from `ChromeDevTools/chrome-devtools-mcp`).
- **Deterministic rule ladder** (first match wins): SELF → DOMINANT (top cousin ≥5× #2 by downloads) → NAME_MATCH → SCOPE (unscoped beats `@scope/X`) → UNCERTAIN review bucket (~20% burden accepted). Static rules, no LLM.
- **Republisher denylist**: `@mseep/*`, `@iflow-mcp/*` — extend as uncertain-bucket reviews surface more.
- **Noise filters**: `%21` case-encoded Go duplicates; downloads<10 dropped when any cousin >1,000.
- **Sticky canonical_id** via COALESCE — changes require a governance event; drift is logged, never auto-applied.

**Extensions needed for 200K scale:**

- **GitHub fork lineage** (Phase B/D): the repos API returns `fork=true` + `parent`/`source` — stamp `derived_from` at ingest. Free signal, not currently consumed.
- **Detached-fork detection** (uploads-not-forks): same default-branch tree-hash or tools-schema content hash ⇒ same family even without fork metadata; name-similarity + "fork of X" in README/description as weak corroborators routed to UNCERTAIN, never auto-merged.
- **Family rollup in product**: derived servers inherit a *pointer* to the canonical parent's assessment plus a delta (drift = its own risk signal — a republished fork lagging upstream security fixes is exactly what Sentinel should flag). Family members still count individually toward 200K only if independently assessed; canonical rollup prevents junk inflation from 50 identical republishes.
- Run canonicalizer as a **post-ingest pass per lane** (it's already a daemon w/ --once/--dry-run/--loop), with member_count deltas in the weekly audit.

## 6. Scoring & cost

Student adapter (Qwen2.5-3B + LoRA, leaderboard-selected `bar_passes=True`) batch scoring, extending the weekly delta-mode moat-rescore job (#1468/#1470). Measured cost: 20K imports = $0.33 → full 200K pass ≈ **$3.30–4.00**. Ceilings: **$5/run, $25/mo hard halt** → surface to chairman, never burn. Vast/RunPod jobs stay inside the managed-jobs manifest (DESTROY_READY gate, forensics-before-destroy).

## 7. Freshness & serving at 200K

- 7-day SLA (freshness_gate #1467) at 200K servers ⇒ rescore throughput ≥ ~29K/day sustained or one weekly 200K delta pass. Weekly delta pass is the plan; verify wall-clock on first 100K+ run.
- Big ingests run **tower-side via fly proxy** (1GB Fly machines OOM on large ingests).
- /freshness and facet queries were rewritten once already at 80K (48s→4s); re-benchmark at 100K and 150K milestones, add indexes before user-visible latency, not after.
- scores table: +200K×7/wk ⇒ prune superseded score rows or partition by run; decide at M1.

## 8. Milestones & kill criteria

| Milestone | Target date | Gate |
|---|---|---|
| M1: 100K assessed | 2026-08-15 | fabrication audit = 0; /freshness p95 < 5s; junk-rate sample ≤5% |
| M2: 150K assessed | 2026-09-15 | Phase D precision ≥90% on audit sample before it opens |
| M3: 200K assessed | 2026-10-15 | all M1/M2 gates still green at scale |

Kill criteria: junk-rate >10% on any weekly sample ⇒ freeze the offending lane; cost ceiling breach ⇒ hard halt; fabrication audit >0 ⇒ lane quarantined until root-caused.

## 9. EVAL — adversarial critique vs. known failure classes

| Failure class (precedent) | Risk here | Mitigation | Verdict |
|---|---|---|---|
| Fabricated fields (Glama tool_count, 48.5K rows) | HIGH — 4 new lanes, heterogeneous APIs | §5 unknown≠zero + per-lane fabrication audit query, in CODE not convention | PASS |
| Republish/fork inflation (50 clones of one server) | HIGH — npm tail is full of `@mseep/`-style republishes | §5b canonicalizer ladder + family rollup; assessed-count audited per canonical family | PASS |
| URL ≠ identity (14K sibling-tier stamp) | HIGH — package→repo mapping in Phase C is exactly this trap | ukey=sid; package merge requires repo *content* match, not URL match | PASS |
| Uncalled helper ≠ gate (is_fresh) | MED — Phase D fingerprint gate could be decorative | Precision-audit gate blocks lane *opening*; gate is in the admission path | PASS |
| Volume vanity (junk inflation) | HIGH — "mcp in name" tail is 272K mostly junk | tier=catalogued vs assessed split; only assessed counts; junk-rate kill criterion | PASS |
| Cost runaway | LOW — scoring measured at ~$3.30/200K | ceilings + managed-jobs manifest | PASS |
| Freshness SLA collapse at scale | MED — weekly 200K delta unproven | M1 wall-clock verification before committing to M2 | CONDITIONAL |
| GitHub rate limits stall Phase B | MED | date-sharded queries, 5K/hr authed budget ≈ 3–4 nights per full sweep; acceptable | PASS |
| Merging ≠ shipping | — | every lane PR: deploy AND re-measure /freshness before marking done | PASS |

**Verdict: APPROVED to execute, with two conditions:** (1) M1 must verify weekly-delta wall-clock at ≥100K before Phase D opens; (2) Phase D admission gate ships with its 500-repo precision audit as a PR artifact, not a claim.

## 10. Sequencing for the builder/architect

Directives (never empty): A1 exhaust directory paginators; B1 github_direct lane w/ date-sharding + fabrication audit; C1 npm lane; C2 pypi lane; D1 fingerprint classifier + precision audit harness. Graphify will index this doc — that is intentional; it should steer architect proposals toward these lanes.
