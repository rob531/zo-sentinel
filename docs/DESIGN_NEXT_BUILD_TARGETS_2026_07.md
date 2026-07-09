# Next Build Targets — 2026-07 (post-outage steer)

**Purpose.** Give the architect/anchor_refill explicit, filename-named build
targets so it proposes PLAN work instead of falling back to schema-derived
dashboard permutations (the `build_mcp_*_analysis_dashboard_*` churn that
dominated 2026-07-04..06). The gaps-map extractor recognizes only
`snake_case.py|html` tokens near trigger words — so every target below is
named as a concrete file with a one-line spec + an exemplar to ground it.

**Sequencing (THE LINE, council_roadmap_2026_07_02 + DESIGN_VULN_INTEL_HORIZON).**
Freshness surfaces (P1) land BEFORE any keyed/agent-facing/badge surface.
Nothing signed/keyed ships against data older than its declared SLA. Vuln
claims carry provenance or they don't ship (kill-switches `vuln.enabled` /
`vuln.otx_enabled` already live, both currently ON in prod).

**Builder scope reminder.** The factory builds exemplar-grounded, mostly
self-contained API/view modules well. DAEMONS and multi-file spines
(perspective_snapshot_daemon, ask_corpus_drift_guard) hollow-block — those are
AGENT-built, not factory-built. Do not re-propose them as single-shot builds.

---

## P1 — Freshness (unblocks everything else; agent + factory split)

- `freshness_metadata_api.py` — GET `/api/servers/{id}/freshness` returning
  `{last_scored_at, model_version, sla_days, sla_status}` where sla_status is
  FRESH|STALE computed from `mcp_llm_axis_scores.scored_at` vs a declared SLA.
  Exemplar: `vuln_exposure_api.py` (same per-server GET + provenance shape).
  Read-only, any authed user. This is the surface every keyed/badge feature
  gates on.

## P2 — Vuln / OTX / CVE surfacing (backend already shipped 2026-07-04)

Backend that exists to build on: `vuln_exposure_api.py` (per-server vulns),
`otx_threat_refs.py` (threat_intel_refs + `/api/servers/{id}/threat_refs`),
`vuln_registry_linker.py` (613 links), `threat_intel_refs` table (594 rows).

- `vuln_facet_extension.py` — extend the facet enumerator with two boolean
  facets: `has_known_cve` (server has >=1 vuln_link) and
  `referenced_in_threat_intel` (>=1 non-aggregator threat_intel_ref). Kill-switch
  aware (returns empty when `vuln.enabled` off). Exemplar: `facet_enum_service.py`.
- `server_threat_intel_view.html` — per-server page section rendering vuln_links
  (severity, source_url, match_basis) + threat_intel_refs split curated vs
  `is_aggregator`. Renders INSUFFICIENT when the kill-switch is off. Exemplar:
  `scan_view.html`.
- `vuln_coverage_sla_api.py` — GET `/api/vuln/coverage` returning
  `{registry_total, linked_servers, coverage_pct, newest_advisory_fetched_at}`
  — the internal coverage-SLA metric the horizon doc requires before any paid
  key. Exemplar: `dashboard_summary_api.py`.

## P3 — Deferred until P1 freshness is green (sequencing gate)

- `scorecard_badge_api.py` (PR #1311, CLEAN) — HOLD. Badges are a keyed/public
  surface; council rejected "badges before freshness". Merge only after
  `freshness_metadata_api` is live and STALE-gating works.

---

**Provenance.** Steer authored 2026-07-06 after the 24h tower power outage, on
chairman direction to turn the architect/builder toward the multi-week plan
(Appendix-H + vuln-intel horizon incl. AlienVault OTX/CVE integration). Pairs
with the drain of 52 dashboard-permutation churn PRs the same day.
[auto-anchor source]
