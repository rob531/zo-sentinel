# DESIGN: Vuln-Intel Horizon (months 2-4) — Council ruling 2026-07-02 (second sitting)

*Council of 3 + FATHER, convened after the weeks-1-6 ruling (Appendix H). UNANIMOUS #1 across
all three members: CVE/OSV/GHSA ingestion cross-referenced to the MCP registry. FATHER's
ruling: "it ships in CONTRA's armor at HISTO's factory, and PRO's money only flows after
counsel and measured freshness — we become a vuln-intel product the same way we became a
registry product: provenance first, promises second."*

*This doc is deliberately a KL DESIGN DOC, not spec candidates: the self-refilling anchor
mines it into directives ONLY when the Appendix-H lanes run dry — the ruled sequencing is
enforced by mechanism, not discipline. Module names below become directive candidates
automatically at that point.*

## THE NEW LINE (binding invariant for the vuln-intel era)

No vulnerability claim leaves this system — API, badge, digest, MCP tool, or paid response —
without a verifiable source-URL, timestamp, and match-confidence attached; any claim that
cannot prove its provenance degrades to INSUFFICIENT rather than ship. We sell provenance or
we sell nothing. (Origin: the 2026-07-02 live CVE-query bug — fragment-matched garbage with
confident citations, fixed same night ONLY because retrieval is deterministic.)

## Horizon sequence (owners + binding gates)

1. **CVE/OSV/GHSA ingestion spine** — FACTORY modules + AGENT surface wiring.
   Deterministic matching ONLY (exact package/repo/version identifiers; NO fuzzy/embedding
   match). Every linkage row: source_url, fetched_at, match_confidence, feed (osv|ghsa|nvd).
   Live kill-switch (policy key `vuln.enabled`) degrading the whole vuln surface to
   INSUFFICIENT. Week-1-style gates re-run on vuln tables before anything downstream reads.
   Future factory modules when the anchor mines this doc: `osv_feed_ingestor.py` (OSV JSON
   batch pull, idempotent upsert into vuln_advisories), `vuln_registry_linker.py`
   (deterministic repo/package identity join advisories x mcp_server_registry into
   vuln_links with confidence), `vuln_exposure_api.py` (GET /api/servers/{id}/vulns with
   provenance fields, INSUFFICIENT degrade), `vuln_facet_extension.py` (known-vuln facet in
   facet_enum_service from vuln_links).
2. **Coverage SLA + discovery revival** — DATA-OPS. Rescore backfill (risk_tier=unassessed
   for all 80,539 rows today), discovery sources reopened, daily freshness probe. SLA is
   INTERNAL-ONLY until measured green 4 consecutive weeks; no published SLA before measured
   sustained freshness — no exceptions, including marketing copy.
3. **Monetization (keyed API + Stripe + org plans)** — AGENT, dark-flagged build in month 2;
   first paid key month 3+ ONLY when: (a) counsel-reviewed ToS with liability cap +
   "informational, not security advice" language, (b) CVE ingestion stable 4 weeks,
   provenance fields >=99% populated, one kill-switch drill in anger, (c) freshness green.
   Rationale (CONTRA, adopted verbatim): a wrong CVE linkage after payment is defamation
   with a receipt.
4. **Self-submission intake** — FACTORY intake/validation + AGENT workflow. SELF-ATTESTED
   labeling only; submissions quarantine-tiered; cannot alter a published score without the
   same integrity gates as scraped data. "Verified publisher" REJECTED until a staffed
   identity process exists.
5. **CI GitHub Action ("mcprisky-check")** — AGENT, one narrow versioned artifact.
   Advisory-only forever this horizon (exit code never blocks a merge); annotations cite the
   same provenance fields as the API. A distribution channel, not an "ecosystem".

## Explicit rejections (do not resurrect without a new council)

- SFT v4 retrain this horizon (coverage before model vanity; month 5+ with adversarial
  no-regress gate).
- Ask v3 embeddings retrieval (converts the findable CVE-bug class into silent unfindable
  corruption; Ask stays citation-or-refuse on deterministic retrieval).
- "Verified publisher" badges (no staffed identity process = no verification claims).
- Blocking CI mode.
- Architect treewalk automation without a hard novelty/dedup gate first (anchor-exhaustion
  churn trap in a new hat).
- Any published SLA before 4 weeks of measured freshness.

## Learnings loop

Builds in these lanes record lessons via build_lessons + mesh_memory design_note rows tagged
`vuln_intel_horizon` so graph consumers traverse module -> ruling -> gates. The mesh_memory
note for this sitting: tag `council_horizon_2026_07_02`.
