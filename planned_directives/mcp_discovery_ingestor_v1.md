# MCP Discovery Ingestor v1 — Planned Directive

**Status:** DRAFT — do not queue until commit 2 (Gate 8 feedback loop) is
shipped and has produced at least 24h of clean cohort data.

**Author context:** This is the sober rewrite of a "firehose" proposal that
would have mass-ingested 10,000+ MCPs into `mcp_server_registry` with
`verdict=NULL`. The enrichment pipeline today produces near-uniform
verdicts across existing rows (signal_analyser quality issue, 5/6 signals
have 1 distinct value), so flooding the registry would multiply low-quality
output without improving anything. v1 is deliberately bounded.

---

## Preconditions that MUST be true before queueing

1. Commit 2 of Gate 8 feedback loop is live (quarantine + circuit breaker
   + directive regeneration signal path).
2. Signal analyser quality has been investigated and either fixed or
   explicitly deferred with a known workaround (e.g., filter to
   `confidence >= 0.85` before admitting rows to risk_register).
3. At least one full 24h cycle has elapsed with gate_8 observing cohorts
   and no circuit breaker trips.
4. A new DuckDB table `mcp_discovery_staging` exists (schema below) —
   this is a separate migration directive that runs BEFORE this one.

---

## Staging table schema (separate prerequisite directive)

```sql
CREATE TABLE IF NOT EXISTS mcp_discovery_staging (
    staging_id          VARCHAR PRIMARY KEY,     -- md5 of normalized URL
    source              VARCHAR NOT NULL,        -- 'github' | 'official_registry'
    discovered_at       TIMESTAMP WITH TIME ZONE DEFAULT now(),
    github_repo_url     VARCHAR,
    canonical_name      VARCHAR,
    description         VARCHAR,
    stars               INTEGER,                 -- null if not from github
    topics              VARCHAR,                 -- JSON array as string
    raw_metadata        VARCHAR,                 -- JSON blob of whatever the source returned
    promotion_status    VARCHAR DEFAULT 'pending', -- 'pending' | 'promoted' | 'rejected'
    promoted_at         TIMESTAMP WITH TIME ZONE,
    promoted_server_id  VARCHAR,                 -- FK into mcp_server_registry once promoted
    rejection_reason    VARCHAR                  -- if promotion_status='rejected'
);
CREATE INDEX IF NOT EXISTS ix_staging_status ON mcp_discovery_staging(promotion_status);
CREATE INDEX IF NOT EXISTS ix_staging_source ON mcp_discovery_staging(source);
```

Rationale for a staging table: preserves spec §5 signal invariant (every
row in `mcp_server_registry` entered via the signal ingestion path) by
giving discovery its own namespace. Promotion to the registry becomes a
deliberate second step that can be gated, rate-limited, or audited
independently of discovery.

---

## The directive JSON (to be queued after preconditions)

```json
{
    "task": "mcp_discovery_ingestor",
    "description": "Build mcp_discovery_ingestor.py. PURPOSE: Bounded, observable discovery of new MCP servers from trusted sources. Writes to mcp_discovery_staging only, NOT mcp_server_registry. Promotion is handled by a separate daemon. SCOPE v1: official_registry + github ONLY (Glama + Smithery deferred to v2). SOURCES: (a) Official registry JSON at https://registry.modelcontextprotocol.io (path confirmed at build time via a HEAD request; if returns 404 or non-200, log WARN and skip source); (b) GitHub search API: GET /search/repositories?q=topic:mcp-server+topic:modelcontextprotocol&sort=updated&per_page=100. AUTH: GitHub requires env var GITHUB_TOKEN; if missing, log ERROR and exit 2 (no silent unauthenticated degradation). RATE LIMIT: minimum 2 seconds between any external HTTP call; GitHub API is additionally capped at 30 req per sweep to stay well below 5000/hr authenticated ceiling. VOLUME CAP: maximum 100 new staging rows per sweep across all sources combined; if cap hit, log INFO 'sweep volume cap reached' and stop. CADENCE: 6h sleep between sweeps (matches gate cadence so each sweep produces one cohort of staging rows observable by the next gate run). DEDUPE: before writing a staging row, query write_service for existing staging_id (md5 of normalized URL) and existing mcp_server_registry row with same github_repo_url; skip if either exists. NORMALIZATION: lowercase URL, strip trailing slash, strip '.git' suffix, compute staging_id as md5 of normalized URL. WRITES: all DB ops via write_service:8772 (never direct DuckDB); insert via /write endpoint with table='mcp_discovery_staging'. HEARTBEAT: every 60s to service_health as 'mcp_discovery_ingestor' with cycle_sec=21600. PROCESS MGMT: nohup launched from go.sh, NOT supervisord. SMOKE TEST CONTRACT: must import cleanly with no top-level network calls; must have main() that exits 0 on GITHUB_TOKEN missing with a clear stderr message; must have a _normalize_url() function testable in isolation; must NOT start uvicorn or bind any port.",
    "complexity": "medium",
    "phase": "13",
    "priority": 2,
    "handler": "zo-backend-coder"
}
```

### Notes on the directive above

- `complexity: medium` not high: single-purpose daemon, few external
  APIs, clear contract. Should fit a MiniMax generation well.
- `phase: 13` (integration), not the invented `phase: 28` from the
  original proposal. 13 is where other integration ingestors live.
- `priority: 2` — runs after spec-appendix core directives.
- `handler: zo-backend-coder` — same as other daemons.

---

## Companion directive: the promotion daemon (queue AFTER the ingestor
is observed working for 48h)

```json
{
    "task": "mcp_discovery_promoter",
    "description": "Build mcp_discovery_promoter.py. PURPOSE: Gates promotion of staging rows into mcp_server_registry. Reads from mcp_discovery_staging WHERE promotion_status='pending'. PROMOTION RULES: (a) github source: require stars >= 3 AND description non-empty; (b) official_registry source: auto-promote; (c) else: mark rejection_reason='source_not_whitelisted'. BATCH SIZE: promote maximum 20 rows per run. INSERT into mcp_server_registry with: server_id = md5(url), registry_source = staging.source, verdict = NULL, first_seen = staging.discovered_at, metadata = staging.raw_metadata. Update the staging row to promotion_status='promoted' with promoted_server_id set. CADENCE: 1h. HEARTBEAT as 'mcp_discovery_promoter'. SMOKE TEST: must NOT bind any port, must read GITHUB_MIN_STARS from env with default 3, must have main() that exits 0 if staging table is empty.",
    "complexity": "medium",
    "phase": "13",
    "priority": 3,
    "handler": "zo-backend-coder"
}
```

Rationale for separating discovery from promotion: lets you observe what
the firehose pulls in BEFORE it touches the assessment pipeline. If
GitHub topic search is noisy (forks, abandoned repos, spam), you see it
in staging and tighten the promoter rules without re-architecting the
ingestor.

---

## What this directive pair deliberately excludes

- **Glama.ai scraping.** No public API documented. HTML scraping is
  brittle and likely to break. v2 at earliest, and only if someone
  confirms a stable endpoint.
- **Smithery.ai scraping.** Same reason.
- **Dependency graph traversal.** "MCP X depends on MCP Y" discovery is
  tempting but expands scope massively. Separate future directive.
- **Automatic enrichment trigger.** The ingestor does NOT poke the
  enrichment pipeline. Enrichment daemons find new rows on their own
  polling cycle. Keeps write paths unidirectional.
- **Any Claude/Anthropic API calls.** The ingestor must not invoke
  inference. All LLM work happens downstream in the enrichment path,
  which has its own cost controls.

---

## Risks I still see even with this scoped version

1. **Enrichment amplification.** Even 100 new staging rows/sweep = 400
   rows/day. If promoter runs hourly promoting 20 at a time, that's
   potentially 400 new registry rows/day. Each row triggers enrichment.
   Before queueing, estimate: current enrichment daemon throughput?
   Current inference cost per row? If 400 rows/day × $0.02/row enrichment
   = $8/day BYOK on top of baseline. Check against budget.

2. **Staging table unbounded growth.** 100 rows/sweep × 4 sweeps/day =
   400/day. After a year, 146k rows. Add a retention policy: drop
   rejected rows after 30 days, keep promoted rows indefinitely
   (they've become server_ids anyway).

3. **GitHub topic is user-declared.** Anyone can tag a repo
   `topic:mcp-server`. Promotion rule of `stars >= 3` is minimal signal.
   May want to add `stars >= 3 AND last_commit_within_180d` to filter
   abandoned or spam repos. Consider for v1.1.

4. **Official registry schema unknown.** I've never inspected what
   `registry.modelcontextprotocol.io` returns. The builder will guess.
   **Pre-work before queueing: curl the registry once manually, confirm
   the response shape, and embed an example response in the directive
   description.** This is a lesson from today — "scrape their directory"
   without a concrete example produces hallucinations.

---

## Pre-queue checklist

- [ ] Commit 2 shipped, Gate 8 feedback loop live
- [ ] 24h of clean cohort data observed
- [ ] Signal analyser quality investigated (fix or documented workaround)
- [ ] Staging table migration directive built, tested, applied
- [ ] Manual curl of `registry.modelcontextprotocol.io` captured; example
      response embedded in the ingestor directive description
- [ ] GITHUB_TOKEN provisioned and added to `.zo_env`
- [ ] Retention policy for staging table designed
- [ ] BYOK cost estimate against current enrichment throughput
- [ ] Review whether enrichment pipeline needs rate limiting in its own
      right before we start feeding it 400 rows/day

---

## When to reconsider the full firehose

If after 2-4 weeks of the v1 discovery ingestor running, all of these
are true:

- Gate 8 shows <10% failure rate on new builds
- Signal analyser produces distinguishable verdicts (>3 distinct values
  across recent assessments)
- Enrichment pipeline sustains >100 rows/day without BYOK overrun
- Staging-to-registry promotion produces useful trust scores

...THEN consider v2 with Glama+Smithery adapters. Until then, the pitch
of "10,000+ MCPs" is a feature in marketing and a bug in operations.