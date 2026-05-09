# ZO-SENTINEL Architecture Documentation

> Imported verbatim from `/home/workspace/zo_sentinel/ARCHITECTURE.md` on
> zocomputer at the time this repo was created. Future edits should
> happen here in the repo, not on zocomputer.

## 1. System Topology

### 1.1 Inter-Daemon Communication Bus

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                           ZO-SENTINEL TOPOLOGY                              │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│  ┌──────────────┐     ┌──────────────┐     ┌──────────────┐                 │
│  │   Scanner    │────▶│              │     │    Risk      │                 │
│  │   Daemon     │     │              │     │    Ranker    │                 │
│  └──────────────┘     │              │     └──────────────┘                 │
│          │            │              │            │                         │
│          ▼            │  write_service│            ▼                        │
│  ┌──────────────┐     │   :8772      │     ┌──────────────┐                 │
│  │   Signal     │────▶│              │────▶│    Trust     │                 │
│  │   Analyser   │     │   (SOLE      │     │  Synthesiser │                 │
│  └──────────────┘     │   STATE BUS) │     └──────────────┘                 │
│          │            │              │            │                         │
│          ▼            │              │            ▼                         │
│  ┌──────────────┐     │              │     ┌──────────────┐                 │
│  │ Enrichment   │────▶│              │────▶│   Verdict    │                 │
│  │ Producers    │     │              │     │   Aggregator │                 │
│  └──────────────┘     └──────┬───────┘     └──────────────┘                 │
│                              │                                              │
│                              │ ONLY external call                           │
│                              ▼                                              │
│                      ┌──────────────┐                                       │
│                      │  inference_  │                                       │
│                      │   router     │                                       │
│                      │   :8773      │                                       │
│                      └──────────────┘                                       │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

### 1.2 Port Registry and Responsibilities

| Port | Service | Role | Dependencies |
|------|---------|------|--------------|
| 8772 | write_service | **Sole state bus** - all read/write for DuckDB | All daemons write here |
| 8773 | inference_router | **Only permitted external API call** | Called by trust_synthesiser |
| 8774 | *(reserved)* | | |
| 8775 | email_guid_auth | GUID token generation for auth workflows | write_service |
| 8776 | manual_override_api | Analyst override endpoints | write_service |
| 8777 | advanced_filter_api | Filtering/search endpoints | write_service |
| 8779 | forensic_detail_api | Deep inspection endpoints | write_service |
| 8780 | approval_workflow | Multi-stage approval state machine | write_service |
| 8781 | registry_api | MCP server registry CRUD | write_service |
| 8782 | search_api | Full-text search endpoints | write_service |
| 8784 | nl_query_engine | Natural language query interface | write_service, inference_router |
| 8785 | rule_engine_api | Policy rule evaluation | write_service |
| 8790 | ui_server | Sentinel dashboard (frontend) | All services via REST |

### 1.3 External Call Policy

**RULE**: `inference_router :8773` is the **only** service permitted to make
direct external HTTP calls.

All other daemons:

* MUST NOT make outbound HTTP calls except to write_service/query_service.
* MUST NOT call `requests.get()` or `requests.post()` to external URLs.
* MUST use the web-scraper skill for external enrichment when needed.

---

## 2. Signal Invariant Contract

Every signal produced by any enrichment daemon MUST conform to this contract:

```python
{
    "signal_type":   "<string>",   # REQUIRED - canonical signal name
    "confidence":    "<float>",    # REQUIRED - 0.0 to 1.0
    "evidence_blob": "<dict>",     # REQUIRED - free-form evidence container
    "server_id":     "<string>",   # REQUIRED - FK to mcp_server_registry
    "scored_at":     "<string>",   # REQUIRED - ISO 8601 timestamp
}
```

### 2.1 Confidence Bands

| Range       | Interpretation                                |
|-------------|-----------------------------------------------|
| 0.00 - 0.25 | Low confidence — signal weak or insufficient  |
| 0.25 - 0.50 | Moderate confidence — signal present, noisy   |
| 0.50 - 0.75 | Good confidence — signal reliable             |
| 0.75 - 1.00 | High confidence — signal definitive           |

### 2.2 Canonical Signal Types (enforced by trust_synthesiser)

`supply_chain_score`, `domain_trust_score`, `community_signal`,
`temporal_stability`, `permission_scope_score`,
`tool_description_safety`, `injection_resilience`,
`evidence_density`, `registry_breadth`, `context_efficiency`,
`vendor_concentration`, `traffic_fingerprint`.

---

## 3. Append-Only Immutability Rules

### 3.1 Append-only tables (no UPDATE / DELETE permitted)

| Table | PK | Rationale |
|-------|-----|-----------|
| `mcp_server_registry`     | server_id                     | Server identity never changes; deprecation via status field |
| `mcp_signal_scores`       | server_id + signal_name       | Historical record of all scoring events                     |
| `mcp_threat_associations` | server_id + threat_type       | Threat history for forensic analysis                        |
| `audit_log`               | id (auto-increment)           | Immutable audit trail                                       |

### 3.2 State-transition tables

| Table             | Mutation Policy                                         |
|-------------------|---------------------------------------------------------|
| `mcp_risk_register` | UPDATE allowed for risk_rank recomputation only       |
| `auth_tokens`     | UPDATE allowed for `used`/`used_at` flags only          |
| `service_health`  | UPDATE allowed for `last_heartbeat` only                |
| `exemptions`      | UPDATE allowed for `status` transitions only            |

### 3.3 Enforcement

The write_service `/execute` endpoint rejects DML with `UPDATE` /
`DELETE` against immutable tables. Audit-style updates use append
inserts with a sequence id.

---

## 4. Freshness SLA Windows

| SLA              | Target | Breach action |
|------------------|--------|----------------|
| First verdict    | 24h    | `first_verdict_sla_breach` alert; escalate to on-call analyst |
| Re-verdict       | 7d     | server marked STALE; auto-queued for re-assessment             |
| Signal freshness | 48h    | per-signal staleness alert                                     |

Re-verdict triggers (immediate, bypass SLA): new threat association,
trust-score drift > 0.3 from baseline, security event reported
(`rug_pull`, CVE), analyst request.

---

## 5. Heartbeat Requirements

Every daemon MUST report heartbeats every 60s to the
`service_health` table via write_service:

```python
POST http://127.0.0.1:8772/write
{
  "table": "service_health",
  "rows": { "service": "<name>", "last_heartbeat": "<ISO UTC>" },
  "wait": true
}
```

Death threshold: 2h with no heartbeat → CRITICAL alert and supervisord
restart attempt. The repo's `ui_server.py` and `approval_workflow.py`
both ship a 30s heartbeat thread by default.

---

## 6. Out-of-Scope Boundary

In scope: discovery, signal collection, trust synthesis, threat ingest,
verdict generation, audit logging, analyst workflows, the dashboard.

Out of scope (handled by external systems):

* HTTP/API gateway or proxy functions
* Blocking or enforcing verdicts at the network layer
* Runtime security controls (WAF, IDS, RASP)
* User authentication or RBAC (delegated externally)
* Secret/vault management
* Network firewall rules or segmentation
* Container orchestration / service mesh

ZO-SENTINEL produces recommendations and signals; downstream systems
(AIDR, ServiceNow, GitHub, Slack/email, custom webhooks) consume them
and enforce policy.

---

## 7. Consistency Guarantees

* **Eventual consistency.** Signal writes visible within 60s, verdict
  updates within 5min of last signal, risk recomputation within 1h.
* **No distributed transactions.** Daemons operate independently.
* **Last-write-wins** for timestamp-based fields; **highest-rank-wins**
  for risk recomputation.

---

## 8. Network Boundaries

* All internal services bind to `127.0.0.1`.
* Externally reachable: `ui_server :8790` (analyst dashboard),
  `registry_api :8781` (CI/CD), `inference_router :8773` (outbound only).

---

## 9. Operational Quick Reference

| Operation              | Command                                  |
|------------------------|------------------------------------------|
| Start all daemons      | `start_all.sh`                           |
| Check daemon status    | `python3 watch.py`                       |
| View pipeline health   | `python3 pipeline_health.py`             |
| Manual server scan     | `python3 mcp_scanner.py --server-id <id>`|
| Force re-assessment    | `python3 signal_analyser.py --force`     |
| Export compliance      | `python3 compliance_exporter.py`         |

---

*Document version: 1.0 — imported into the `zo-sentinel` repo at v1.0.0-baseline-recovery*
