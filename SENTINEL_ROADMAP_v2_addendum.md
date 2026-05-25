# Sentinel Roadmap v2 — Addendum: Economics, Cold-Start, and Repositioning Canonical ID

**Status:** Written 2026-04-19 immediately after roadmap v2, in response to three sharp operator challenges:

1. Canonical naming may be a moat, not a dead end
2. Access requests need at-request enrichment ("output flow")
3. Storage and compute can't grow without bound — can't become a petabyte DB

This addendum refines v2 rather than replacing it.

---

## 1. Canonical ID: repositioned, not shelved

**v2 position (incorrect):** shelve canonical_id entirely; propagation via
edges is simpler and industry avoids canonicalization.

**Revised position:** canonical_id is **the glue** that makes Sentinel's
other signals compound. Not load-bearing in isolation; load-bearing when
combined with operator identity + endpoint trust + threat intel.

Think of it this way. The industry has:
- Package metadata (Snyk, Dependabot)
- Vulnerability data (OSV, NVD)
- Cross-registry coverage (ecosyste.ms)
- Operator identity ... *nobody*, rigorously
- Endpoint/data-flow analysis ... *nobody*, for MCPs
- All four correlated ... *nobody*

If Sentinel has all four correlated **and** canonical_id as the join key,
then: a CISO asking "what do we know about mcp-server-kubernetes" gets a
response that spans all 3 registry variants, operator identity (Flux159,
has public github profile, domain X), all endpoints (kubernetes API
control plane only, no third-party proxies), threat intel clean on all
endpoints, aggregated threat history across variants. **That's a unique
product no other tool provides.** Canonical_id is what makes the
aggregation possible.

**Decision:** Phase III.d (new) — "canonical identity resolution, informed
by endpoint + operator data." Runs AFTER endpoint extraction completes,
because endpoint data makes canonicalization much more confident
(identical endpoint set is strong evidence of shared identity).

Sequence:
1. Phase II: directory ingestion (Anthropic reference list anchors trust)
2. Phase III.a: endpoint extraction (the unlock)
3. Phase III.b-c: endpoint classification + operator identity
4. Phase IV.a-c: negative signals (threat intel, domain provenance,
   typosquat)
5. **Phase III.d: canonical identity** — now informed by endpoint
   fingerprints, operator evidence, and verified cross-registry links.
   Sticky with drift log as originally designed; but rules now have more
   evidence to apply, making them higher-confidence.

---

## 2. Output flow: at-request enrichment with tiered depth

**The problem v2 glossed over:** when an access request arrives for an
MCP not yet in our registry, how does Sentinel respond? v2 implicitly
assumed bulk pre-enumeration of all MCPs. But that's not realistic
long-term and doesn't match law-firm workflow (attorneys need answers
in minutes, not days).

**Architectural split — two enrichment tiers:**

### Tier 1: At-request (hot path, budget 10 seconds)

Runs inline during access request. Produces *preliminary verdict* with
explicit confidence level. Signals available:

| Signal | Latency | Notes |
|---|---|---|
| WHOIS/RDAP on endpoint domain | 1-3s | cacheable for 24h |
| npm/pypi package metadata | 1-5s | cacheable for 6h |
| Threat feed lookup (against cached feeds) | <100ms | pure local DB |
| Directory presence | 1-2s | single HTTP HEAD per directory |
| DNS resolution + ASN lookup | <500ms | cacheable |
| **Total budget** | **~10s** | Acceptable for access-request UX |

Output: preliminary verdict tagged `TIER_1_PRELIMINARY`, with explicit
note "based on N signals; full assessment queued." The MCP is written to
registry with status=`PENDING_TIER_2`.

### Tier 2: Background deepening (24-48h)

Runs async after first-seen. Signals:

| Signal | Latency | Why background |
|---|---|---|
| Source fetch + static analysis | 30-300s | too slow for hot path |
| Endpoint extraction (full) | 60-180s | requires source |
| TLS cert history | 5-30s | crt.sh can be slow |
| Cross-registry canonical resolution | 30s | needs all of above |
| Operator identity verification | 10-60s | multiple lookups |
| Historical signal context | varies | needs time-series |

Output: upgrades preliminary verdict to `TIER_2_COMPLETE` verdict. If
Tier 2 contradicts Tier 1 (e.g., Tier 1 said TRUSTED but Tier 2 finds
third-party proxy), emit a `verdict_revised` event for operator
review.

### User experience

```
Access request: "can Alice use @example/mcp-notion"?

T+0s:  Request received
T+8s:  Preliminary verdict: NEEDS_REVIEW (tier 1)
       Evidence: domain registered 2 years ago, npm downloads 45k,
       not in Anthropic reference list, no threat matches,
       directory mentions: 2 (mcp.so, awesome-mcp)
       Confidence: LOW (tier 1 only)
       ETA for full assessment: ~24 hours
T+6h:  Tier 2 background enrichment completes
       Endpoints extracted: api.notion.com, api.openai.com
       Operator: matches notion.com entity, clean
       Threat intel: no matches on any endpoint
T+6h:  Verdict upgraded: TRUSTED_RESEARCH (tier 2)
       Confidence: HIGH
       Notification to requester: "full assessment complete, 
       provisional approval converted to standard approval"
```

This is honest UX: show preliminary assessment fast, show full
assessment when ready, flag divergence between them.

### Implementation

New module: `tier1_inline_enricher.py` — invoked by external API
`/access_request/` endpoint. Separate from background T1 agents.
Shares threat_feed_cache and caches with them (common state).

New directive: `seed_tier1_inline_enricher.json` — defines the 10s
budget, the fallback behavior per signal, the preliminary verdict
contract.

---

## 3. Cost discipline — first-class, not a footnote

### Current footprint (measured)

- ZOMesh DuckDB: **104 MiB** after ~2 weeks of operation
- 790 MCPs × partial enrichment

### Projected at 5,000 MCPs (realistic 2-3 year horizon)

With naive retention: ~30 GB of DB, 75-100 GB of source cache if we
retain unpacked trees. Tractable but growing faster than it should.

### Retention policy (enforced via scheduled vacuum job)

**Signal history:**
- Last 30 days: full daily granularity
- 30-365 days: weekly rollups (median, p95)
- 1-5 years: monthly rollups
- 5+ years: dropped

Implementation: a monthly `mcp_signal_history_compactor` daemon. Reads
raw enrichment history, writes rollup rows, drops detail rows older
than the window.

**Source cache:**
- **Ephemeral by default.** Fetch, extract, write endpoint evidence
  with file:line references, delete source tree.
- Re-fetch on demand if evidence needs re-auditing.
- Exception: keep sources for MCPs flagged UNCERTAIN or BLOCKED — the
  forensic value justifies the storage.

**Threat feed cache:**
- 30-day retention. Feeds naturally age out; old C2 domains no longer
  matter.
- Daily refresh of active indicators; weekly purge of expired.

**Raw API responses:**
- Never stored. Extract needed fields, discard raw.
- Already practicing this with ecosyste.ms (we store top-cousin fields,
  not full cousin list).

**Drift logs:**
- Kept indefinitely (they're governance records), but size is tiny.

### Compute discipline

**LLM-free hot path.** Every directive written 2026-04-19 is LLM-free
in its steady-state operation. MiniMax invoked only:
- Once during directive→module build (produces code, ~$0.01 one-shot)
- Optionally for ambiguous admin review items (human-in-loop, small
  volume, bounded)

**Re-extraction cadence.** Source extraction is the heavy operation.
Policy: re-extract only when package version changes OR 90 days elapse,
whichever first. Most MCPs don't version-bump monthly. Realistic
re-extraction: 5-10% of registry/month = 250-500 MCPs = manageable.

**Rate budgets per upstream:**

| Source | Daily budget | Current usage |
|---|---|---|
| ecosyste.ms | 600 req/hr (12% of limit) | ~50/day steady |
| npm registry | 1 req/sec | negligible |
| pypi | 1 req/sec | negligible |
| WHOIS/RDAP | 1 req/sec per TLD | at-request + background |
| crt.sh | 1 req/5sec | Tier 2 only |
| Threat feeds | 5 daily downloads | background only |

**Compute budget target: fits on a single VPS.** If Sentinel ever needs
a cluster, we've over-built. Pick boring infrastructure: one VPS, one
DuckDB file, scheduled vacuum jobs, boring. If storage exceeds 50 GB
or compute sustained >50% CPU, re-architect.

### Specific anti-patterns to avoid

1. **Don't store raw HTML from directory scrapes.** Parse, extract,
   discard.
2. **Don't cache full ecosyste.ms cousin lists.** Top cousin + ecosystems
   observed suffices.
3. **Don't log every signal-bridge cycle's output.** Log cycle summaries,
   not per-row decisions (write_queue_log got huge; already saw this).
4. **Don't retain inference_log indefinitely.** LLM calls are rare; last
   90 days of detail, older aggregated.
5. **Don't run enrichment modules every 5 minutes.** Most signals are
   slowly-changing. Daily is usually sufficient; hourly for threat intel
   only.

---

## Revised prioritization

Given the three refinements:

1. **Pilot harness** (meta-directive, ~2 hours) — foundation for
   everything else
2. **Tier 1 inline enricher** (new, ~1 day) — solves cold-start for
   access requests; uses existing signals + live WHOIS
3. **Directory ingestor** (Phase II, ~4 hours) — anchors trust via
   Anthropic reference list
4. **Threat feed cache + lookup** (Phase IV.a piece, ~1 day) — enables
   both at-request and background threat checks
5. **Endpoint extractor** (Phase III.a, ~2-3 days) — unlocks operator
   identity and proper endpoint trust
6. **Endpoint classification + operator identity** (Phase III.b-c, ~2
   days)
7. **Domain provenance + typosquat** (Phase IV.b-c, ~1 day)
8. **Canonical identity v2** (Phase III.d, ~1 day) — now informed by
   all above; simpler than original Commit B because evidence is stronger
9. **Signal history compactor** (cost discipline, ~4 hours) — runs
   monthly, prevents unbounded growth

Total: ~12 days of focused work to move Sentinel from "decent package
metadata tool" to "operator-aware, endpoint-trust-weighted, CISO-usable
registry that answers access requests in under 10 seconds
with full assessment within 24 hours."

---

## Non-negotiable principles (adding to v2)

* **Enumeration discipline** (from v2): <10% of upstream rate limits,
  polite headers, aggressive caching
* **Dual-lens verdict** (from v2): negative signals dominate positive
* **Operator-weighted verdict** (from v2): final stop is the biggest
  signal
* **Tier 1/Tier 2 split** (new): at-request <10s budget; background
  24-48h for deepening
* **LLM-free hot path** (new): inference only in build-time and
  human-in-loop review; never per-request
* **Bounded growth** (new): enforced retention policy; single-VPS
  compute budget; no raw source retention; no unbounded history
* **Canonical identity as glue** (new): revisited in Phase III.d once
  endpoint and operator data make it meaningful

---

## Open questions for next session

1. **WHOIS rate limiting.** At-request WHOIS means each access request
   costs us one or more WHOIS lookups. If Alice submits 20 requests in
   a minute, do we burn 60 WHOIS queries? Need per-requester rate limit
   on the API layer.
2. **Tier 1 verdict persistence.** If Tier 1 says TRUSTED and the access
   is granted, and Tier 2 later finds a third-party proxy, does
   Sentinel retroactively revoke? Need a policy on verdict revision
   in flight.
3. **Source availability.** Some MCPs don't publish source (closed-source
   binaries, proprietary hosted services). Phase III.a doesn't work for
   them. What verdict do we emit — NEEDS_REVIEW permanently, or a
   special TIER_2_INCOMPLETE that's acceptable for internal MCPs?
4. **Directory de-duplication.** If `mcp-server-kubernetes` appears in 5
   directories, do we count that as 5 mentions (signal boost) or 1
   (same MCP multiple times)? Needs canonical_id to answer cleanly —
   which is why canonical_id is back on the roadmap.

---

## What this addendum does NOT change

* Commit A (ecosyste.ms integration) remains shipped and valuable.
* Signal_bridge, write_service, circuit breaker all remain.
* Directive-driven development pattern continues.
* The pilot-first rule applies to every new module.

What it DOES change:
* Adds Tier 1 inline enrichment as a priority before bulk enumeration.
* Adds retention/vacuum as a first-class concern.
* Restores canonical_id to the roadmap as Phase III.d.
* Makes LLM-free hot path an architectural principle, not an implicit
  choice.