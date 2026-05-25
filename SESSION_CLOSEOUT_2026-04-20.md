# Session Closeout — 2026-04-20 (Monday, Extended)

**Written:** 2026-04-20 ~14:30 UTC, appended ~19:00 UTC
**Purpose:** Honest account of what worked, what didn't, and what Monday's Robin needs to do next.

---

## TL;DR (updated)

Three ingestors touched today. Two cleanly shipped (anthropic_reference, alienvault_otx), one shipped-with-bug-then-cleaned (mcp_registry). Zero OTX→MCP matches produced — this is a registry-shape finding, not a pipeline failure. Hardware/infrastructure discussion produced a clear plan for the tower purchase but no purchase yet.

**Net value banked today:**
- 11 Anthropic-vouched trust anchors (anthropic_reference)
- 50 valid mcp_registry mentions (after cleanup of 351 false positives)
- 5000 MCP Registry facts rows for future analysis
- 343 OTX IOCs in threat_feed_cache with severity + pulse attribution
- Three working ingestor modules proven end-to-end
- One concrete bug to fix next session (match strategy)
- Clear hardware purchase framework (specific reasons, specific caveats)

---

## Morning session: the two directory ingestors

[Detailed account retained in earlier section — see below]

---

## Afternoon addendum: OTX ingestion

### What shipped

`otx_ingestor.py` — 551-line one-shot ingestor for AlienVault OTX subscribed pulses.

Design properties (for future-Robin or future-Claude):
- Reads API key from env `Alienvaultapi` — never logs, stores, or echoes the value
- Two write destinations: `threat_feed_cache` (raw IOCs with feed_name='alienvault_otx') + `mcp_threat_associations` (specific MCP findings with source='alienvault_otx')
- Parameterized SQL throughout (fixes the f-string pattern in the existing `threat_feed_cache.py`, which has a noted SQL injection vector on adversarial hostnames — separate cleanup task)
- Severity inference from pulse tags (malware/apt/c2→critical, phishing/exploit→high, etc.)
- `--smoke`, `--max-pulses N`, `--no-match` flags
- MAX_PAGES=100 safety ceiling (100 * 50 = 5000 pulses max)
- 1s inter-page sleep respects OTX 60/min free-tier rate limit
- Cross-reference phase pulls all registry URLs once, intersects in Python (efficient at scale)
- **Explicit scope boundary in code header**: ZOMesh/Sentinel MCP trust only; never cross-reference against firm/operational data

### First bounded run results

```
pulses=20 indicators=419 host_indicators=52 matches=0 write_errors=0 elapsed=8.7s
```

DB state after run:
- `threat_feed_cache` with feed_name='alienvault_otx': 343 rows (76 deduped via UPSERT — same indicators across multiple pulses)
- Indicator type mix: 101 SHA256 + 75 MD5 + 55 SHA1 + 44 URL + 33 hostname + 19 domain + 13 CVE + 3 IPv4
- `mcp_threat_associations` with source='alienvault_otx': **0 rows**

### Why zero matches (important finding)

Not a pipeline bug. A **registry shape** finding:

`mcp_server_registry` contains 805 entries pointing at just THREE host domains:
- www.npmjs.com: 554 entries
- smithery.ai: 140 entries
- github.com: 111 entries

OTX threat intel doesn't flag npmjs.com/github.com/smithery.ai as malicious infrastructure — those ARE the package registries, not compromised servers. The OTX indicators we got (phishing domains, C2 servers, malware hashes) have no structural path to match against our current registry URLs.

The matching layer is **architecturally incapable of producing matches today**, not just missing them. To unlock OTX value, we need match targets OTHER than registry URL domains:

1. **`mcp_registry_facts.primary_identifier`** (package names) — but ALL 5000 rows have null primary_identifier because the MCP Registry list endpoint returns empty packages arrays (yesterday's known bug). OTX sometimes flags specific malicious npm/PyPI packages by name.
2. **Package tarball SHA256 hashes** — would need new plumbing to fetch + store per-package hashes from npm/PyPI. 101 SHA256 indicators in OTX could then match.
3. **Package author handles** (github.com/USER portion) — speculative, low yield.

### User action taken in parallel

Robin is expanding OTX subscriptions to follow pulse authors focused on supply chain intel (npm malicious packages, PyPI typosquat trackers, MCP-adjacent threats). Subscription quality is the primary lever to increase OTX relevance to our specific surface.

---

## IMMEDIATE ACTIONS (when you have moments)

### Already done:
- ✅ Cleanup script ran: 401→50 mentions, 351 false positives removed
- ✅ OTX bounded run complete, 343 IOCs banked

### Pending, in priority order:

**1. Expand OTX pulse subscriptions** (5-10 min on otx.alienvault.com)
   - Search keywords: `npm malicious`, `pypi supply chain`, `typosquat`, `malicious package`
   - Subscribe to AlienVault official + a handful of supply-chain-focused researchers
   - Re-run `python3 /home/workspace/zo_sentinel/otx_ingestor.py` after subscribing

**2. Fix the MCP Registry detail endpoint investigation** (30-60 min, next session)
   - `/v0/servers/{name}` returned 404 for every tested name yesterday
   - Try: `?name=X` query param, UUID-based id, check Swagger docs directly
   - Once packages hydrate, re-run Ingestor 2 with proper match strategy — also unlocks OTX match path (1) above

**3. Fix the `threat_feed_cache.py` f-string SQL injection vector** (15 min)
   - `upsert_indicators()` and `check_indicator()` interpolate hostnames directly into SQL
   - Adversarial feed data could exploit this
   - Parameterize using same pattern as `otx_ingestor.py`

**4. Fix Ingestor 1 relative-URL cosmetic issue** (5 min)
   - Active reference-server candidates stored as `src/fetch` instead of full GitHub URL
   - One-line normalization in candidate-write path

---

## WHAT'S LEFT FOR A FUTURE SESSION

### Signal quality work (this is the big ticket)

`tool_description_safety` and `permission_scope` both have 3 distinct values across 781 MCPs. That's not a signal, it's a category. Fixing these is the single highest-leverage Sentinel task, and it's the primary workload justification for the hardware purchase (see below).

### Hardware decision (deferred but scoped)

Framework settled:
- **Target:** Dell Precision 3630 Tower (NOT XPS 8930 — PSU ambiguity and Dell proprietary form factor, NOT OptiPlex SFF — chassis can't take GPU)
- **Build:** Precision 3630 + RTX 3060 12GB + 32GB DDR4 starter (add second stick later if needed) + NVMe + 460W PSU verified
- **Budget:** $500-650 depending on source
- **Sources in order of preference:** r/hardwareswap, eBay (filter: Refurbished + Top Rated Seller + 'Precision 3630 Tower' + '460W' in listing), Dell Refurbished Outlet direct
- **Avoid:** Walmart/Amazon third-party refurb listings (omit PSU wattage = sellers not rigorous enough for GPU use case)

Strong justifications (in priority):
1. Directive-loop extension — Claude Desktop as second worker on shared queue, removes Robin from low-level bash toil
2. Signal fine-tuning capacity — fix tool_description_safety + permission_scope with locally-trained small models, data sovereignty (CISO-sensitive corpus)
3. Research/experimentation surface — ASI-Evolve and similar
4. Insurance capacity — MiniMax price changes, ZoComputer outage, sensitive-matter inference

Weak justifications (dropped from rationale):
- "Bigger models" generically (no named workload today)
- Cost savings on inference (MiniMax flat-rate beats electricity for raw inference)

Pre-purchase checklist:
- [ ] Write the directive-loop architecture spec before buying (what's a directive file, how does Claude Desktop pull, sync mechanism)
- [ ] Verify 460W PSU on specific unit being purchased
- [ ] Start with 32GB RAM (one 32GB stick), not 64GB
- [ ] Buy from source with return policy
- [ ] Budget 4-6 hours weekend setup time (Cloudflare Tunnel, MCP filesystem server, Claude Desktop config, Ollama)

ASI-Evolve specifically: framework is plausible, numbers in the summary (105 SOTA architectures, +18 MMLU) I can't verify without reading the actual paper. Treat as "interesting to play with on local hardware" not as load-bearing justification. A 3060 can run proof-of-concept experiments, not reproduce headline results.

---

## Files state (updated)

**Live and useful:**
* `/home/workspace/zo_sentinel/mcp_reference_servers_ingestor.py` — working
* `/home/workspace/zo_sentinel/mcp_registry_ingestor.py` — working, match bug documented
* `/home/workspace/zo_sentinel/otx_ingestor.py` — working, 343 IOCs banked, zero MCP matches (registry shape finding)
* `/home/workspace/zo_sentinel/_cleanup_registry_false_matches.py` — already run, idempotent
* `/home/workspace/zo_sentinel/threat_feed_cache.py` — existing, has f-string SQL vector (flagged for fix)
* `/home/workspace/zo_sentinel/RISK_CURVE_HYPOTHESIS.md` — design note
* `/home/workspace/zo_sentinel/SENTINEL_ROADMAP_v2.md` — reference

**Tables state:**
* mcp_directory_mentions (anthropic_reference): 11 rows ✓ clean
* mcp_directory_mentions (mcp_registry): 50 rows ✓ clean (after cleanup)
* mcp_discovery_candidates (anthropic_reference): 9 rows ✓
* mcp_discovery_candidates (mcp_registry): 4599 rows ✓
* mcp_registry_facts: 5000 rows ✓ (primary_identifier all null pending detail-endpoint fix)
* threat_feed_cache (alienvault_otx): 343 rows ✓ banked
* mcp_threat_associations (osv): 75 rows ✓ (source=null bug, separate cleanup needed)
* mcp_threat_associations (alienvault_otx): 0 rows (registry shape finding)

**Scope boundary banked to memory 2026-04-20:**
> ZOMesh/Sentinel work stays within the MCP trust-intelligence domain. Do NOT blur into law-firm operational risk register, firm infrastructure, client domains, or any work-Robin operational security concerns. If a threat intel / risk data source is being integrated, the cross-reference target is always Sentinel-internal (mcp_server_registry, mcp_registry_facts, etc.), never the firm.

---

## Lessons banked (extended)

1. Always verify raw source format before writing parser (README bullet bug)
2. Always run `--smoke` before full crawl when available (Ingestor 2 packages-empty finding)
3. Match strategies must consider cardinality of match target (false-positive magnet bug)
4. Terminal disconnect during long run is survivable with idempotent writes (deterministic MD5 IDs + UPSERT)
5. Reverse-DNS tail matching is dangerous when tail is a common word
6. Capture rich metadata (facts table) even when match layer has bugs
7. Documented APIs may have endpoints that 404 in practice; always probe
8. **NEW: Zero matches is information, not failure.** 343 OTX IOCs + 0 MCP matches told us more about the registry shape bottleneck than any synthetic test could have.
9. **NEW: Subscription curation is the primary lever for feed quality.** A pipeline that can't filter upstream is at the mercy of what you subscribed to. Invest time in subscription hygiene.
10. **NEW: Cross-reference architecture has to match the SHAPE of what you're joining.** Our registry stores host-of-package-registry (npmjs.com); OTX stores host-of-malicious-infrastructure. These are incommensurable categories. The fix isn't better matching code — it's different match targets.

---

## Where Sentinel stands architecturally

**Phase II (Directory Ingestion):** ✅ reference README done, mcp_registry shipped with known bug, OTX wired in
**Phase III (Endpoint Trust):** blocked on nothing, not started
**Phase IV.a (Threat Intel):** ✅ URLhaus/OpenPhish/PhishTank from Sunday + OTX today; needs better cross-reference targets to produce associations at scale
**Phase IV.d (Risk Curves):** design captured, implementation deferred

Signal quality snapshot (unchanged from Sunday):
- supply_chain: 34 distinct values (healthy)
- community_signal: 33 distinct (healthy)
- domain_trust: 11 distinct (OK)
- temporal_stability: 3 distinct (flat — sparse source data)
- tool_description_safety: 3 distinct (flat — parsing problem, fine-tuning target)
- permission_scope: 3 distinct (flat — parsing problem, fine-tuning target)

---

## Honest self-assessment of full day

**Did well:**
- Caught Sunday's LLM-generated directory_ingestor bugs before running it
- Verified sources before writing parsers (after the first miss)
- Three working pipelines, all idempotent and safe to re-run
- Captured risk curve hypothesis as design doc before jumping to implementation
- Pushed back on hardware purchase until reasoning was concrete
- Held the scope boundary firmly after Robin flagged "never two"
- Recognized zero OTX matches as data, not failure

**Did badly:**
- Guessed at README bullet format from HTML render rather than raw markdown
- Wrote registry ingestor match strategy without cardinality analysis (351 false positives)
- Didn't smoke Ingestor 2 before committing to full crawl
- Let two listings (OptiPlex SFF, XPS 8930) consume shopping time when the specific failure modes were predictable from the product category
- Nearly blurred MCP risk register into firm risk register (corrected by user's "never two")

**Value/cost:** Strong positive. Three pipelines landed, ~400 valid trust+threat anchors banked, hardware framework articulated, scope boundary codified. Could have been cleaner at 2-3 decision points.

---

Next session pickup point: expand OTX subscriptions → re-run → check match count. Then tackle detail-endpoint investigation. Then (when hardware arrives) start signal-fine-tuning corpus work.

Enjoy the rest of your day. Everything here can wait.