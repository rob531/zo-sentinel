# ZO-SENTINEL

**Trust intelligence for Model Context Protocol (MCP) servers — built by an autonomous agent pipeline.**

ZO-SENTINEL is two things at once:

1. **A product** — an intelligence layer that assesses MCP servers and assigns a deterministic security **verdict**, for a security architect deciding whether a given MCP server is safe to deploy.
2. **A self-building system** — the code in this repository is continuously *proposed, built, graded, and opened as pull requests* by an autonomous agent pipeline. Most modules here are machine-generated; humans review and merge.

> ⚠️ **Experimental research system.** The repository root holds hundreds of autonomously-generated modules of varying maturity. The curated, structured core lives under [`zo_sentinel/`](zo_sentinel/). Treat root-level `*.py` files as generated artifacts, not a hand-maintained, stable API.

---

## The product — what it does

Sentinel discovers MCP servers from public registries, scores each across a set of independent **trust signals**, combines them into a composite, and assigns one of six verdict tiers (plus a data-gap state).

It is an **intelligence layer only**: it produces signals, verdicts, and detection artifacts for *other* systems to consume. It does **not** proxy MCP traffic, authenticate users, or enforce policy at call time. See [`SENTINEL_SCOPE_BOUNDARY.md`](SENTINEL_SCOPE_BOUNDARY.md).

### Verdict taxonomy

| Verdict | Composite | Meaning |
|---|---|---|
| `TRUSTED_GENERAL` | > 75 | Approved for general enterprise use |
| `TRUSTED_RESEARCH` | > 60 | Safe for research / exploratory use |
| `ENTERPRISE_CONTROLLED` | > 45 | Acceptable with documented security controls |
| `CAUTION_LIMITED` | > 30 | Requires additional review |
| `HIGH_RISK_ISOLATED` | > 15 | Sandboxed environments only |
| `KNOWN_THREAT` | ≤ 15 | Matched a known-threat signal |
| `INSUFFICIENT` | — | Too many signals missing to assess (a data-gap state, not a risk tier) |

### Signal model

Each server is scored across independent signals — *domain trust, tool-description safety, permission scope, supply chain, community, temporal stability, prompt-injection resilience,* and ecosystem-enrichment signals. Every signal producer emits a row with the invariant shape:

```json
{ "signal_type": "<snake_case_name>", "confidence": 0.0, "evidence_blob": { } }
```

The authoritative signal list and scoring contract live in [`PRODUCT_SPEC.md`](PRODUCT_SPEC.md) and [`ARCHITECTURE.md`](ARCHITECTURE.md).

---

## The system — how the code gets built

```
directive architect → promoter → builder (LLM coding agent) → build artifact → governor → publisher → auto/build/* PR → CI gates → human merge
```

- **Directive architect** proposes build tasks ("directives") into `directives/proposed/`.
- **Promoter** ([`zo_sentinel/promoters/`](zo_sentinel/promoters/)) validates and moves directives to `directives/pending/`.
- **Builder** generates each file via an LLM coding agent.
- **Governor / ingestor** ([`zo_sentinel/ingestor/`](zo_sentinel/ingestor/)) grades each build artifact and quarantines failures.
- **Publisher** ([`zo_sentinel/publisher/`](zo_sentinel/publisher/)) opens an `auto/build/*` pull request per artifact, labelled `autonomous-build`.
- **CI gates** ([`.github/workflows/pr-gates.yml`](.github/workflows/pr-gates.yml)) run `ruff`, a hermetic smoke-ladder, a front-end check, and a schema-drift (capmap) check on every PR.

Machine output must pass the gates **and a human merge** — there is deliberately **no auto-merge**. A read-only triage bot ([`tools/pr_triage.py`](tools/pr_triage.py)) classifies the open PR queue (`solid` / `dup` / `scaffold` / `stale`) to make review fast.

### Shared state

All daemons share state **exclusively** through a single write-service over a DuckDB store — modules never open the database directly. Core tables include `mcp_server_registry`, `mcp_signal_scores`, `mcp_signal_enrichments`, `mcp_definition_history`, `mcp_threat_associations`, and `mcp_risk_register`. See [`DB_SCHEMA.md`](DB_SCHEMA.md).

---

## Repository layout

| Path | Contents |
|---|---|
| [`zo_sentinel/`](zo_sentinel/) | Curated core package — `promoters/`, `publisher/`, `ingestor/`, `evaluators/`, `mcp_servers/`, `probes/`, `schemas/` |
| [`tools/`](tools/) | Operational scripts — CI helpers, the PR-triage classifier, janitors |
| [`tests/`](tests/) | CI smoke-ladder + gate tests (hermetic — stand up their own mocks, no external services) |
| [`directives/`](directives/) | The autonomous build-task queue (`proposed/` → `pending/`, plus completion sentinels) |
| `.github/workflows/` | `pr-gates`, `pr-triage`, `evaluator`, `fetch-failures` |
| `*.py` (root) | Autonomously-generated product modules — scanners, scorers, enrichers, APIs, daemons. Generated; maturity varies. |
| `*.md` (root) | Design specs, architecture notes, and the agent's own engineering journals |

---

## Key documents

- [`ARCHITECTURE.md`](ARCHITECTURE.md) — authoritative system design
- [`PRODUCT_SPEC.md`](PRODUCT_SPEC.md) — product contract (signals, verdicts, invariants)
- [`SENTINEL_SCOPE_BOUNDARY.md`](SENTINEL_SCOPE_BOUNDARY.md) — what Sentinel is and is not
- [`DB_SCHEMA.md`](DB_SCHEMA.md) — the data model

---

## Status

Active, autonomous, and experimental. Pull requests labelled `autonomous-build` are machine-generated and pending human review. The structured core under `zo_sentinel/` is the part to read first.
