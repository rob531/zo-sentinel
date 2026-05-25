# Sentinel Scope Boundary — Intelligence, Not Enforcement

**Created:** 2026-04-22
**Status:** Authoritative — supersedes any conflicting scope statement in older docs
**Audience:** future-Robin, future-Claude, directive generator prompt builders

---

## The one-sentence version

**Sentinel is an intelligence layer about MCP servers. It is not a gateway, proxy, portal, or enforcement plane.**

Everything that follows is elaboration of that sentence.

---

## What Sentinel does

- Ingests information about MCP servers from registries, threat feeds, vulnerability databases, ecosystem metadata, and direct scanning
- Computes trust signals per server across multiple dimensions (supply chain, community, temporal stability, permission scope, tool description safety, domain trust)
- Synthesises per-server trust verdicts with evidence trails
- Exposes this intelligence via API + UI for consumption by humans AND by other systems (portals, gateways, governance planes, CISO dashboards)

## What Sentinel does NOT do

- **Route MCP traffic.** That is a gateway's job (Cloudflare, Kong, Envoy-based solutions).
- **Mediate client ↔ server communication.** That is a portal's job (Cloudflare MCP server portals, future equivalents).
- **Authenticate users.** That is an identity provider's job (Okta, Auth0, Cloudflare Access).
- **Enforce policy at call time.** That is an enforcement plane's job (AI Gateway, AiDR-style products).
- **Block or throttle traffic.** That is a WAF's job.
- **Host MCP servers.** That is the developer platform's job.
- **Provide SSO/MFA.** See "Authenticate users" above.
- **Define enterprise DLP rules.** Sentinel can produce signals that feed DLP decisions; Sentinel does not make DLP decisions.

## Why this boundary matters

### Market is saturated with enforcement/transport plays

As of April 2026, the MCP enforcement space is crowded:

- Cloudflare: remote MCP hosting + Access + portals + AI Gateway + WAF + Shadow MCP detection
- CS AiDR: commit-time policy enforcement with verdict gates (this product's discussions originally inspired Sentinel's creation — Sentinel was scoped as the intelligence complement, not a reimplementation)
- Datadog: MCP server monitoring
- A wave of startups building MCP gateways, policy engines, and proxies

Competing in that market means building infrastructure that incumbents with network effects will win. Sentinel would lose.

### Intelligence is the underserved layer

Everyone building gateways and portals needs a source of trust intelligence. They need to answer questions like:
- Is this third-party MCP server safe to add to our portal?
- Has this npm package that publishes an MCP server been compromised?
- How does the community reputation of Server A compare to Server B?
- What's the temporal stability signal for this server over the last 90 days?
- Does this server's tool description contain prompt injection indicators?

No one else is building this as a first-class product. Sentinel can be the intelligence source of record.

### The Forrester insight, applied

Forrester noted MCP "functions more like transport or interoperability mechanisms, comparable to RPC or messaging systems rather than policy engines" (InfoQ, 2026-04-22). Applied to Sentinel: the protocol is transport. The portals/gateways/WAFs are enforcement. **Trust intelligence is a third, separable layer** — and it is precisely what governance planes need as an input.

---

## Decision rule for future MCP-adjacent news

When news arrives about MCP (new gateway, new portal, new security product), ask three questions in this order:

1. **Does it change what signals we can extract?** (e.g., a new threat feed, a new vulnerability database, a new registry with metadata we don't have) — RELEVANT, evaluate for ingestor work.
2. **Does it produce detection artefacts we can adopt?** (e.g., JSON-RPC fingerprints, tool schema patterns, known-bad indicators) — RELEVANT, evaluate for scanner/enrichment work.
3. **Does it define a consumer for Sentinel output?** (e.g., a portal that could consume our trust verdicts via API) — RELEVANT, evaluate for output format / integration.

If the answer to all three is no — it's a gateway/portal/proxy play, and it's **out of scope**. Note it, move on. Do not generate directives to match features.

---

## Applied: Cloudflare enterprise MCP announcement (2026-04-22)

- Remote MCP server hosting — **out of scope**, that's a developer platform.
- Cloudflare Access auth — **out of scope**, that's identity.
- MCP server portals — **out of scope**, that's enforcement/discovery. BUT: portals are a potential **consumer** of Sentinel output (Q3 in the decision rule). Note for integration roadmap.
- Code Mode — **out of scope as a feature to build**, BUT: whether a server exposes Code Mode patterns is a signal about its architectural quality. Potentially a future signal dimension once weak signals are fixed (deferred).
- Shadow MCP detection via JSON-RPC regex — **RELEVANT (Q2)**. Detection artefacts (fingerprints) are directly adoptable into Sentinel's scanner layer. This becomes directive candidate.
- AI Gateway — **out of scope**, that's an inference proxy.
- Cloudflare Gateway DLP — **out of scope** as a tool to build, BUT the regex patterns they publish are adoptable (same as shadow MCP).

**Net takeaway:** one directive candidate (fingerprint detection module), one potential future signal dimension (deferred), one integration roadmap note (portals as consumers).

---

## What to do when the scope line feels fuzzy

If a feature feels like it might cross the boundary, apply this test:

> **Does this feature sit between the MCP client and MCP server at runtime, or does it operate on information about MCP servers out-of-band?**

- **At runtime between client and server** → enforcement/transport → **out of scope**
- **Out-of-band on information about servers** → intelligence → **in scope**

Examples:
- Scanning a registry page for an MCP server's package metadata → out-of-band → in scope
- Intercepting a JSON-RPC call to check if the method is allowed → at runtime → out of scope
- Computing a trust score from 6 enrichment signals → out-of-band → in scope
- Blocking a tool call because the trust score is low → at runtime → out of scope
- Publishing a verdict API that a portal queries before allowing the tool call → out-of-band → in scope (the portal is doing enforcement, Sentinel is providing intelligence)

---

## The integration roadmap (future work, not current)

As portals mature, Sentinel's output should be consumable by them. This is NOT current work, but flagged so future directives don't accidentally box out the integration path:

- Trust verdict API returning standardised verdict objects (server_id, verdict, score, confidence, evidence_summary, last_updated)
- Webhook subscriptions for verdict changes (portal subscribes, gets notified when a server's verdict changes)
- Bulk export formats (CSV, JSON Lines, OpenVEX-like structured attestations)
- Potential future: native connector plugins for Cloudflare MCP portals, Kong, Envoy (but only after the intelligence layer is proven — do not build integrations for products we don't have real users of)

---

## Lesson banked from the first scope pressure test

On 2026-04-22, an MCP-adjacent Cloudflare announcement arrived. The initial impulse was to evaluate features for adoption. The better response was to separate features into three buckets (ingest/detect/consume) using the rule above, and adopt only what produced signals or detection artefacts.

**If the impulse is "we need to match this feature," the answer is almost always "no, we need to make sure this feature can consume our output."**

---

## How this doc should be used by the directive generator

The directive generator's prompt (`sentinel_directive_generator.py → build_prompt`) should reference this scope boundary explicitly so the LLM does not propose directives that cross the line. A one-line addition to the prompt preamble is sufficient:

> "Sentinel is an intelligence layer, not a gateway/portal/proxy. Directives must produce signals, trust verdicts, ingestors, or intelligence consumption APIs. Directives that propose routing, authentication, policy enforcement, or traffic mediation are OUT OF SCOPE and will be rejected."

A follow-up directive can wire this into the prompt builder. Not today — the directive_gen is healthy, and adding scope guards is a medium-complexity change that should go through a design pass first.