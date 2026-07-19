# DESIGN: canonical_family (restoring the Commit-B canonical promise)

## Lineage

Feb-Apr 2026, a predecessor session built the **Commit-B canonicalizer**
(`canonicalizer.py`, `mcp_project_canonicalizer.py`, `fixes/pilot_canonicalization*`):
every registry row gets a canonical project identity so github twin + npm
listing + pypi listing aggregate for threat rollup. Its locked doctrine:

1. **Deterministic rules only** - no LLM, no per-record classifier.
2. **Sticky assignment** - once set, changed only by governance, never silently.
3. **Drift detected, not auto-applied** - disagreements are reported.
4. **Provenance stamped** - every assignment records the rule that made it.

The engine that powered its richer rules (cached **ecosyste.ms** cousin KV,
`mcp_ecosystems_metadata`) did **not survive the mesh DB rebuild** - verified
2026-07-18: no canonical/ecosystems tables in the write-service DB, only the
15-server pilot sample JSON remains. The DuckDB-era canonicalizer was never
ported to the Fly Postgres app.

## What this PR does

Ports the surviving doctrine to prod with the deterministic subset of rules:

- **Migration 0010**: `canonical_family` (indexed) + `canonical_rule` +
  `canonical_set_at` on `mcp_server_registry`.
- **`tools/canonical/family_rules.py`**: the family-key contract, identical to
  the rule used by the 7/16-7/18 duplicate analyses (metadata.repository ->
  url -> `pkg:self/<sid16>`), frozen by `tests/test_canonical_family_rules.py`.
- **`tools/canonical/materialize_canonical_family.py`**: idempotent sticky
  backfill (`--apply` fills NULL rows only; second run provably writes 0) and
  `--rederive` drift report (detect, never auto-update).

Why it matters (2026-07-18 measurements): 232,174 rows / 162,832 inferred
families = 29.9% dup overhead, ~92% cross-source; the "59,924 never-scored"
backlog proved to be URL-duplicates (45 real). With the key materialized,
family-level rollup, dedup accounting, and change-event grouping become
single-query operations instead of offline scripts.

## Future lanes (not this PR)

- **Ecosystems re-enrichment**: re-fetch ecosyste.ms cousins (fetcher survives
  at `ecosystems_metadata_fetcher.py`) and upgrade `url`/`self` rows to
  bridge-aware families under rule name `ecosystems`; sticky contract means
  upgrades go through the drift report.
- **Family-aware surfaces**: /freshness families count, family facet, and
  score_change_events grouped by family ("project changed" vs "sibling row").
- **Governance pass** for the uncertain bucket, honoring
  `mcp_project_canonical_uncertain` semantics.
