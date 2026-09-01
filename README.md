# MCPRisky — Trust Intelligence for Model Context Protocol Servers

**Live app:** [mcprisky.io](https://mcprisky.io/app)

MCP servers are becoming the integration layer through which AI agents reach
external systems — file stores, ticketing, code hosts, internal APIs. Adopting
one means granting an autonomous system a set of tool permissions against your
environment, usually on the strength of a registry listing and a README.

There is no established assessment standard for that decision. This project is
one: a working trust and assurance pipeline that scores MCP servers across
seven risk dimensions, routes them through an analyst approval workflow, and
produces the attestations and audit trail an enterprise third-party risk
program would expect for any other vendor.

> Built and maintained independently. Not affiliated with any employer.

---

## What it does

**Discovers and fingerprints.** Scans MCP registries, imports server
definitions, and fingerprints tool schemas so that changes after approval are
detected rather than assumed away.

**Scores across seven signals.** Each server carries a synthesised trust score
built from:

| Signal | What it measures |
|---|---|
| `domain_trust` | Reputation and provenance of the hosting domain |
| `tool_description_safety` | Risk language and intent in advertised tool descriptions |
| `permission_scope` | Breadth of access the server requests relative to purpose |
| `supply_chain` | Dependency integrity, CVE exposure, blast radius |
| `community_signal` | Adoption, maintenance velocity, contributor base |
| `temporal_stability` | Behavioural and schema consistency over time |
| `injection_resilience` | Measured resistance to prompt injection payloads |

Scores are tracked as a time series, so trust degradation after approval
surfaces as a trend rather than a surprise.

**Governs the decision.** Analysts issue APPROVED / CONDITIONAL / REJECTED
verdicts through a workflow backed by a policy rule engine. Every verdict
generates an attestation and a human-readable explanation. Exemptions are
managed explicitly, decisions are written to an immutable audit log, and
outcomes populate a risk register.

**Watches the supply chain.** CVE enrichment, npm typosquat detection,
dependency chain auditing, blast radius calculation, cross-registry
correlation, server impersonation detection, rug-pull monitoring, and vendor
concentration analysis.

**Tests injection resilience.** A harness executes injection payloads against
approved servers under quarantine controls. The corpus is ingested and triaged
through an LLM-mediated review step before promotion to the live test set;
results feed the `injection_resilience` signal back into the trust model.

---

## Architecture

Python 3.11 throughout. FastAPI for HTTP services, plain Python for daemons.
All persistence routes through a single write service rather than direct
database access, which keeps every mutation auditable and serialised.

```
      registries / npm / GitHub
                 │
        ┌────────▼────────┐
        │  scan + finger  │   discovery, schema fingerprinting
        └────────┬────────┘
                 │
        ┌────────▼────────┐
        │ signal analysis │   7 scorers + enrichment pipeline
        └────────┬────────┘
                 │
        ┌────────▼────────┐
        │ trust synthesis │   weighted score, time-series trend
        └────────┬────────┘
                 │
        ┌────────▼────────┐
        │ analyst verdict │   policy engine, approval workflow
        └────────┬────────┘
                 │
        ┌────────▼────────┐
        │   attestation   │   signed record, audit log, risk register
        └─────────────────┘
```

Supporting layers: a scheduler tier for reassessment and staleness cleanup, a
threat intelligence ingestion and correlation pipeline, a metrics and health
monitoring layer with per-daemon heartbeats, and a mesh runtime that supervises
process health and repairs data inconsistencies.

**Storage.** A 42-table operational datastore covering the server registry,
per-signal scores, fingerprints, attestations, decisions, submissions,
exemptions, the risk register, policy rules, schema history for drift
detection, threat associations, and the audit trail.

---

## Repository layout

```
zo_sentinel/          core platform
  ├── scanning and signal analysis
  ├── trust synthesis and verdict layer
  ├── prompt injection harness
  ├── FastAPI services (registry, search, dashboard, review, forensics)
  ├── schedulers, monitors, and reporting daemons
  ├── promoters/       candidate promotion pipeline
  └── static/          dashboard and submission UI

zo_mesh/              runtime infrastructure
  ├── process supervision and crash recovery
  ├── write service (single gateway for all persistence)
  └── inference routing with model escalation

world_agent/          autonomous research agent feeding threat context
builder/              directive-driven build system
schema/               live-generated schema reference
docs/                 architecture and design notes
```

---

## Design invariants

These are enforced rather than advisory:

1. All reads and writes route through the write service — never direct
   database connections.
2. High-risk and limited-confidence verdicts can never be auto-committed to
   downstream systems. Human decision required.
3. No interactive prompts in generated code. Every decision is mediated and
   logged.
4. A build task is complete only when its declared output exists on disk and
   passes a syntax and import gate.
5. Injection corpus material stays quarantined until explicitly promoted.

---

## Status

| Area | State |
|---|---|
| Discovery, signals, trust pipeline | Complete |
| Prompt injection harness | Complete |
| Enterprise integration | In progress |
| Analyst UI and submission portal | Live |

Enterprise integration work in flight: ServiceNow inbound webhooks with OAuth
and signature validation, a CrowdStrike AI Defense Runtime commit gateway with
hard safety gates, and signed evidence bundles per analyst decision.

---

## Why this exists

Third-party risk management has decades of established practice —
questionnaires, evidence review, contractual controls, periodic reassessment.
Almost none of it has been adapted to a supply chain where the thing you are
onboarding is a set of tool permissions granted to an autonomous system that
can be manipulated through its own inputs.

This is an attempt to work out what that practice should look like by building it, rather than waiting for a standard to arrive.