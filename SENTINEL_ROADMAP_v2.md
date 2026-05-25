# ZO-Sentinel Roadmap v2

**Status:** Draft, written 2026-04-19 after the Commit A session and the
"who's behind the final stop?" reframe conversation.

**Purpose:** Reset the Sentinel direction after realizing that package-metadata
work (commits 1-4 + A) covered the smaller half of what a CISO actually needs
to evaluate an MCP access request.

---

## The reframe in one paragraph

Sentinel's original framing treated MCPs the way Snyk/Dependabot treat npm
packages: assess the code, score its supply-chain quality, ship a verdict.
That's useful but insufficient. When a CISO evaluates an access request
("can Alice use MCP X?"), the dominant question isn't "is the code well-
maintained?" — it's **"when this MCP is connected, where does my firm's
data end up, and who's the data controller at the final stop?"**. A pristine
open-source package that ships queries to an unverified third-party proxy
is a worse risk than a modestly-maintained package that calls the named
provider's API directly with the user's own credentials. Package metadata
is an input; operator identity and data path are the decision.

---

## Architectural doctrine

### Enumeration discipline (principle, not negotiable)

ZO-Sentinel's data collection must never exceed 10% of any upstream source's
stated rate limit, must identify itself via User-Agent + From headers, must
cache aggressively (24h minimum TTL for slowly-changing data), and must
prefer depth (re-querying known entries) over breadth (mass enumeration)
when rate budget is tight. Failed enumerations back off exponentially.
When in doubt: be boring, be predictable, be the traffic the upstream
source wishes everyone was. Getting null-routed or rate-limited hurts
future-us. Looking like reconnaissance hurts present-us.

Concrete targets: ecosyste.ms steady state = 600 req/hour max (their
ceiling is 5000); WHOIS/RDAP = 1 req/second with 2-second jitter; TLS
cert transparency = batch via daily crt.sh dumps, not streaming; threat
feed ingestion = daily pull of published snapshots, not live query.

### Dual-lens verdict model

**Positive lens** (what we mostly built so far): "is this MCP well-made,
well-maintained, coming from a reputable source?" Signals: community,
supply chain, domain trust, temporal stability, permission scope. Used
to upgrade verdict from NEEDS_REVIEW toward TRUSTED_RESEARCH / TRUSTED.

**Negative lens** (barely built yet, highest ROI): "does this MCP touch
any known-bad infrastructure, match malicious patterns, or exhibit
suspicious operator characteristics?" Signals: threat intel, domain
provenance, typosquat risk, endpoint trust, operator identity. Used
to override positive signals with BLOCKED verdict.

**Dominance rule:** Negative signals dominate positive ones. An MCP with
high community signal that also matches an active phishing feed gets
BLOCKED, not NEEDS_REVIEW. The inversion matters — this is where Sentinel
differs from every other SBOM tool.

### Verdict should ultimately weight operator, not package

A future Sentinel verdict structure:

* **Package layer** (code quality, supply chain): floor check. Catches
  obviously-broken code. Pass/fail, not graduated.
* **Endpoint layer** (where does data go): primary trust determinant.
  "Calls api.google.com directly with user's key" vs "routes through
  author.com/proxy" are different orders of magnitude of risk.
* **Operator layer** (who runs the endpoints): dominant signal. An
  identical code fork from a trusted operator inherits their trust; a
  fork by an unknown operator does not.

---

## Phase map

### Phase I — Package metadata (mostly complete)

* **Commit 1-2:** Circuit breaker + quarantine for bad enrichments ✅
* **Commit 3:** Resilience — daemon wrapper, liveness probe, write-service
  self-kill on DuckDB invalidation ✅
* **Commit 4:** Signal quality — signal_bridge, Gate 8 relaxation, Gate 9
  signal diversity ✅
* **Commit A:** ecosyste.ms integration — cross-registry cousin metadata,
  downloads-based community_signal, age-based temporal_stability ✅
* **Commit B:** Cross-registry canonicalization — **shelved.** Industry
  doesn't solve it cleanly (Libraries.io, ecosyste.ms, Sonatype all
  struggle); ROI is low relative to phases II-IV; the problem shape
  ("which PURL is canonical?") is less useful than the propagation shape
  ("when a threat lands on one PURL, which others inherit risk?"). If
  ever revived, rebuild as `mcp_identity_edge` (propagation graph, human-
  approved) rather than sticky canonical_id.

Phase I delivered: 3 of 6 signals now well-discriminating (was 0), npm
"dark matter" visible (98 flagship packages with real downloads data), a
resilience layer that survives DuckDB wobbles. Good foundation.

### Phase II — Directory ingestion (next, low-hanging)

Goal: enrich registry with first-party endorsement signals from the
authoritative MCP directories. This is the single biggest trust-boost
we can add with the least effort.

**Sources (priority order):**

1. `modelcontextprotocol.io/servers` — Anthropic's own reference list.
   Presence here is the strongest single trust signal in the ecosystem.
2. `github.com/modelcontextprotocol` organization — Anthropic-maintained
   MCPs. Overlaps heavily with #1, but provides authoritative repo URLs
   and maintainer identity.
3. `github.com/punkpeye/awesome-mcp-servers` — community-curated index.
   Weaker signal (no gatekeeper), but broad coverage.
4. `mcp.so` — aggregator with category tagging.
5. `PulseMCP` — newer aggregator.
6. `glama.ai` MCP directory.

**Data model:**

```sql
CREATE TABLE mcp_directory_mentions (
    id              BIGINT PRIMARY KEY,
    server_id       VARCHAR NOT NULL,
    directory_name  VARCHAR NOT NULL,      -- 'anthropic_reference',
                                           -- 'awesome_mcp', 'mcp_so', etc.
    mention_url     VARCHAR,
    mention_context VARCHAR,               -- section heading, category
    mention_rank    INTEGER,               -- ordering within directory
    first_seen      TIMESTAMPTZ,
    last_seen       TIMESTAMPTZ,
    UNIQUE (server_id, directory_name)
);
```

**New signal: `directory_presence_signal`**

* Presence in `anthropic_reference`: base score 85
* Presence in any 3+ other directories: base score 70
* Presence in 1-2 directories: base score 55
* Absent from all directories: base score 30 (not zero — absence isn't
  proof of malice, just absence of evidence)
* Negative modifier if in directory but published <30 days ago (possible
  fast-listing reconnaissance)

**Cost:** ~10 HTTP requests per 24h cycle. Most directories publish
markdown indexes or JSON APIs.

**New T1 agent:** `mcp_directory_ingestor.py`, 24h cycle, one page per
directory. Lives under daemon_wrapper like the other T1 agents.

### Phase III — Endpoint trust (the big one)

Goal: for each MCP, answer "what external hostnames does this code
connect to, and who's at the other end?" This is where Sentinel becomes
actually differentiated.

**Sub-phase III.a: endpoint extraction**

For each MCP with a known source repo URL, pull the repo (shallow clone
or tarball) and extract outbound hostnames via static analysis:

* Grep for URL string literals
* Grep for `fetch()`, `requests.get()`, `urllib`, `axios`, `got`, `http.get`
* Parse default configs (`.env.example`, config.ts, settings.py)
* Parse README and docs for documented endpoints
* Capture env-var based endpoints (user-configurable) separately

Output: `mcp_endpoints` table, one row per (server_id, hostname) pair
with evidence (file path, context line, detection method).

**Sub-phase III.b: endpoint classification**

For each unique hostname:

* WHOIS via RDAP — registrar, creation date, privacy status
* DNS resolution → IP → ASN lookup
* TLS cert history via crt.sh — first issuance, issuer CA
* Reverse-DNS patterns, HTTPS fingerprint
* Is this hostname in any known-provider allowlist (google.com,
  anthropic.com, openai.com, azure.com, aws.amazon.com, cloudflare.com,
  github.com)? → TIER_FIRST_PARTY
* Is this hostname CDN/infrastructure only (fastly, cloudflare edge)?
  → TIER_INFRASTRUCTURE
* Is this hostname author-controlled (matches npm author domain / github
  author pattern)? → TIER_AUTHOR
* Otherwise → TIER_UNKNOWN

**Sub-phase III.c: signals**

* `endpoint_trust_signal` — composite of tier distribution across all
  endpoints. All FIRST_PARTY = high score; any UNKNOWN = lower; any
  AUTHOR = moderate (depends on author trust).
* `operator_identity_signal` — strongest trust for MCPs where all
  endpoints resolve to named corporate entities with DPAs on file.
* `data_residency_signal` — where do the endpoints physically resolve?
  EU / US / elsewhere.

**Cost:** 1-2 hostnames per MCP × 790 MCPs × 4 lookups each (RDAP, DNS,
ASN, crt.sh) = ~6,000 lookups. Spread over 24h = 250/hr. Fine, if we
respect each source's rate limit individually.

**Dependency:** Phase III needs some MCPs to have fetchable source. Many
npm-only packages bundle source in the tarball; github-sourced ones are
trivial. Phase III only works for ~60-70% of the registry initially.

### Phase IV — Negative-signal enumeration (your sharpest insight)

Goal: proactively identify the risky MCPs, not just filter the safe ones.
This is where Sentinel earns its keep for a CISO. Positive signals
confirm what you already suspect; negative signals surface what you
don't know to look for.

**Sub-phase IV.a: threat intel integration**

Daily pull of these feeds:

* URLhaus — abuse.ch active malware C2 domains
* PhishTank — verified phishing URLs
* OpenPhish — live phishing feed
* Spamhaus DBL — bad domains
* Google Safe Browsing — browser-blocked sites

Store in `threat_feed_cache` table, refreshed daily. For each MCP's
extracted endpoints (from Phase III), check all endpoints against all
feeds. Any match = BLOCKED verdict regardless of other signals.

**Sub-phase IV.b: domain provenance**

For each endpoint hostname:

* Domain age <30 days → flag
* Domain age <90 days + privacy WHOIS → flag
* Registrar in weak-enforcement jurisdiction → flag
* Certificate issued <14 days ago with no prior history → flag

Composite `domain_provenance_signal`. Score 100 = old domain, named
registrant, long cert history. Score 20 = new domain, private WHOIS,
fresh cert.

**Sub-phase IV.c: typosquat detection**

For each MCP name, compute Levenshtein distance against:

* Anthropic reference list names (highest-value targets)
* Top-100 downloaded legitimate MCPs
* Names of MCPs flagged in threat feeds

Distance <=3 with non-identical match → `typosquat_risk_signal` low score.
Examples to catch: `anthropiic-mcp`, `model-contextprotocol-server`,
`mcp-sever-filesystem`, `@modelcontextprotocl/sdk` (one char drop).

Already have scaffolding in `npm_typosquat_alerts` table — extend.

**Cost:** Threat feeds are free public downloads, daily. Typosquat is
local Levenshtein — zero external cost. Domain provenance overlaps
with Phase III.

### Phase V — Runtime observation (future, expensive)

Goal: for the subset of MCPs that pass static checks but remain
ambiguous, observe actual runtime behavior in a sandbox.

Not cheap. Not near-term. Documented here for completeness — this is
what we do when all the cheap signals disagree.

Approach:

* Ephemeral container per MCP
* Canary prompts covering common tool calls
* Capture DNS + outbound connections via sidecar
* Compare observed endpoints to statically-extracted set
* Flag divergence (MCP connecting to undisclosed hosts at runtime)

Reserved for: MCPs flagged as ambiguous after phases II-IV, or spot-
checks on high-risk verdicts before production enablement.

---

## Prioritization

If time is scarce, the sequence that delivers the most CISO value
soonest:

1. **Phase IV.a (threat intel)** — 1 day of work, immediate blocking
   capability, zero-to-hero for "does Sentinel catch the obvious bad?"
2. **Phase II (directory ingestion)** — 1 day, gives us the Anthropic
   reference-list trust anchor.
3. **Phase IV.c (typosquat)** — half a day, uses the reference list
   from Phase II to catch the lookalike attack pattern.
4. **Phase III.a (endpoint extraction)** — 2-3 days, unlocks all of
   Phase III.
5. **Phase III.b + IV.b** — combined, 2 days, gives us real endpoint trust.
6. **Phase III.c** — polish and signal integration.

Eight-ish engineering days to move Sentinel from "decent package metadata
tool" to "actually answers the CISO's question."

---

## Open questions

* **Anthropic reference list format:** is it scrapeable from
  modelcontextprotocol.io/servers as HTML, or is there a JSON API? If
  HTML only, do we need to be extra polite (weekly refresh rather than
  daily)? Verify before committing to schedule.
* **Endpoint extraction for minified npm packages:** some packages ship
  only built JS. Static analysis against minified code is noisy. Accept
  partial coverage or require source to be fetchable via `repository`
  field in package.json?
* **Operator DPA mapping:** do we maintain our own mapping of (hostname
  → corporate entity → DPA status) or rely on public WHOIS? Public WHOIS
  has limits; a curated mapping for top-20 operators (Google, Anthropic,
  OpenAI, AWS, Azure, Cloudflare, etc.) gives much better precision at
  low maintenance cost. Lean curated.
* **Verdict reintegration:** current `trust_synthesiser` combines signals
  linearly. Phase IV negative signals need dominance semantics (one bad
  signal overrides many good ones). Need to extend synthesiser logic.
* **Private/internal MCPs:** a law firm might use bespoke internal MCPs
  that will never appear in directories. Absence-from-directory should
  not be penalized when the submission path marks it as internal.

---

## What doesn't change

* Existing T1 agents keep running.
* Existing gates keep running.
* write_service / signal_bridge / canonicalizer-shelf stay as-is.
* The circuit-breaker and quarantine infrastructure remains the safety
  layer for all new enrichments.
* Directive-driven development pattern continues: new T1 agents and
  enrichment modules are seeded as directives, built by the builder,
  reviewed by operator before going live.