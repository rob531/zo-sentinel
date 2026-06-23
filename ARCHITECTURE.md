# ZO-SENTINEL Architecture Documentation

## Overview

ZO-SENTINEL is an MCP server safety intelligence platform for enterprise InfoSec. It monitors, assesses, and scores MCP servers using trust signals derived from multiple intelligence sources.

---

## 1. Daemon Topology

| Daemon | Port | Type | Responsibility |
|--------|------|------|----------------|
| `write_service` | 8772 | HTTP REST | Central governance layer for all DuckDB reads/writes |
| `inference_router` | 8773 | HTTP REST | ML inference and signal scoring engine |
| `threat_intel_ingestor` | — | Daemon | Ingests threat intel from AlienVault OTX, Shodan, and ecosystem.ms |
| `sentinel_directive_generator` | — | Daemon | Generates and queues security directives |
| `zo_mcp_server` | 8090 | FastAPI/MCP | Primary MCP tool server with FastMCP |

### write_service (Port 8772)
- **Role**: Sole write path to DuckDB data warehouse
- **Responsibility**: All INSERT, UPDATE, UPSERT operations through parameterized JSON API
- **Isolation**: Prevents lock contention and ensures audit trail

### inference_router (Port 8773)
- **Role**: ML inference endpoint
- **Responsibility**: Signal scoring, anomaly detection, pattern recognition
- **Interface**: Accepts JSON payloads, returns scored JSON

### zo_mcp_server (Port 8090)
- **Role**: MCP tool server for Sentinel operations
- **Framework**: FastAPI + FastMCP
- **Tools**: Registry scanning, attestation signing, signal querying

---

## 2. write_service API Contract

**Base URL**: `http://localhost:8772`

### 2.1 Write Operation

```
POST /write
Content-Type: application/json

{
  "table": "target_table_name",
  "rows": [
    {
      "column1": "value1",
      "column2": "value2",
      ...
    }
  ],
  "wait": true
}
```

**Key Rules**:
- `rows` (plural) is required, not `row`
- `wait: true` ensures synchronous confirmation
- All values must be JSON-serializable
- Timestamps must be ISO 8601 strings (not epoch floats)

### 2.2 Query Operation

```
POST /query
Content-Type: application/json

{
  "sql": "SELECT * FROM table WHERE condition = ?",
  "params": ["value"]
}
```

**Key Rules**:
- Parameterized queries only (no f-string interpolation)
- `params` is a list of values for `?` placeholders

### 2.3 Execute Operation

```
POST /execute
Content-Type: application/json

{
  "sql": "CREATE TABLE IF NOT EXISTS ..."
}
```

**Key Rules**:
- DDL operations only
- No user data in DDL

---

## 3. Signal Invariant Requirements

### 3.1 Signal Score Shape

```json
{
  "signal_id": "sha256_deterministic_id",
  "target_server_id": "mcp_server_registry.server_id",
  "signal_type": "threat_intel|behavioral|attestation|dependency",
  "score": 0.0-1.0,
  "confidence": 0.0-1.0,
  "evidence_blob": {},
  "computed_at": "2024-01-15T12:00:00.000Z",
  "ttl_seconds": 86400
}
```

### 3.2 evidence_blob Requirements

The `evidence_blob` MUST contain provenance chain:

```json
{
  "source": "alienvault_otx|shodan|ecosyste_ms|behavioral|attestation",
  "source_url": "https://...",
  "retrieved_at": "2024-01-15T12:00:00.000Z",
  "raw_data": {},
  "analysis": {
    "findings": [],
    "ioc_matches": [],
    "confidence_factors": []
  },
  "attestation": {
    "signer": "principal_name",
    "signed_at": "2024-01-15T12:00:00.000Z",
    "signature": "base64_signature"
  }
}
```

### 3.3 Signal Computed_at Invariant

- MUST be ISO 8601 string with timezone (`Z` suffix)
- MUST NOT be epoch float (enforced by schema type TIMESTAMPTZ)
- Example valid: `"2024-01-15T12:00:00.000Z"`

---

## 4. Heartbeat SLA

### 4.1 Heartbeat Schema

```sql
CREATE TABLE service_health (
  service_name VARCHAR,
  last_heartbeat TIMESTAMPTZ,
  status VARCHAR,  -- 'running', 'stalled', 'crashed'
  meta JSON
);
```

### 4.2 SLA by Daemon Type

| Daemon Type | Heartbeat Frequency | Staleness Threshold |
|-------------|--------------------|--------------------|
| Long-running daemon (while True loop) | Every POLL_SECS cycle | 2x POLL_SECS |
| FastAPI service (uvicorn) | Every cycle | 30 seconds |
| Batch job (one-shot) | N/A (exit 0 on success) | N/A |

### 4.3 Heartbeat Payload

```json
{
  "table": "service_health",
  "rows": [{
    "service_name": "threat_intel_ingestor",
    "last_heartbeat": "2024-01-15T12:00:00.000Z",
    "status": "running",
    "meta": {"cycles": 142, "last_query_count": 50}
  }]
}
```

### 4.4 Implementation Pattern

```python
def run():
    check_single_instance()
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)
    while True:
        cycle()
        send_heartbeat()
        time.sleep(POLL_SECS)
```

---

## 5. Database Schema Reference

### 5.1 Core Tables

**mcp_server_registry**
| Column | Type | Notes |
|--------|------|-------|
| server_id | VARCHAR PK | Deterministic ID (SHA256 of server key fields) |
| name | VARCHAR | Display name |
| url | VARCHAR | MCP endpoint |
| first_seen | TIMESTAMPTZ | ISO 8601 |
| last_seen | TIMESTAMPTZ | ISO 8601 |
| last_scanned | TIMESTAMPTZ | ISO 8601 |
| last_assessed | TIMESTAMPTZ | ISO 8601 |
| trust_score | DOUBLE | 0.0-1.0 |
| status | VARCHAR | 'active', 'suspended', 'revoked' |

**mcp_attestations**
| Column | Type | Notes |
|--------|------|-------|
| attestation_id | VARCHAR PK | Deterministic ID |
| server_id | VARCHAR FK | References mcp_server_registry |
| principal_name | VARCHAR | Attestor identity |
| attested_at | TIMESTAMPTZ | ISO 8601 |
| claims | JSON | Attestation payload |
| signature | VARCHAR | Base64 signature |

**mcp_signal_scores**
| Column | Type | Notes |
|--------|------|-------|
| score_id | VARCHAR PK | Deterministic ID |
| target_server_id | VARCHAR FK | References mcp_server_registry |
| signal_type | VARCHAR | threat_intel\|behavioral\|attestation\|dependency |
| score | DOUBLE | 0.0-1.0 |
| confidence | DOUBLE | 0.0-1.0 |
| evidence_blob | JSON | Provenance chain |
| computed_at | TIMESTAMPTZ | ISO 8601 |

**service_health**
| Column | Type | Notes |
|--------|------|-------|
| service_name | VARCHAR PK | Daemon identifier |
| last_heartbeat | TIMESTAMPTZ | ISO 8601 |
| status | VARCHAR | 'running', 'stalled', 'crashed' |
| meta | JSON | Operational metadata |

---

## 6. Out of Scope

### 6.1 Explicitly Excluded Domains

- **Law firm operational risk register**: Not integrated
- **Firm infrastructure details**: Not referenced
- **Client matters database**: Isolated from Sentinel
- **Work-Robin operational security**: Separate concern
- **Trading account operations**: zocomptrady (acct #889310942) is trading account; Robinhood Investing (acct #886614932) is read-only

### 6.2 Technical Boundaries

- **mesh_memory.db**: SQLite at `/home/workspace/Datasets/zo-mesh/mesh_memory.db` — accessed via `sqlite3.connect()`, NOT write_service
- **DuckDB**: Only accessed through write_service (no direct `duckdb.connect()`)
- **Firm data**: Threat intel cross-references are Sentinel-internal only

### 6.3 Cross-Domain Isolation

Agents operate within strict domain boundaries:
- **Trading domain**: zocomptrady only
- **Sentinel domain**: MCP trust-intelligence only
- **Deputyship domain**: Legal representation only
- **LinkedIn domain**: Social/recruiting only

Cross-domain entity leakage is prevented by anti_entropy_agent enforcement.

---

## 7. Service Discovery

### 7.1 Health Check Pattern

```bash
# Check if daemon is alive
pgrep -f '/zo_sentinel/threat_intel_ingestor.py'
pgrep -f '/zo_mesh/probe_consumer.py'

# Check write_service
curl -s -o /dev/null -w "%{http_code}" http://localhost:8772/health
```

### 7.2 Canonical Paths

| Component | Path |
|-----------|------|
| Sentinel modules | `/home/workspace/zo_sentinel/` |
| Logs | `/home/workspace/logs/{SERVICE_NAME}.log` |
| Mesh memory | `/home/workspace/Datasets/zo-mesh/mesh_memory.db` |
| PID files | `/home/workspace/zo_sentinel/{SERVICE_NAME}.pid` |

---

## 8. Security Invariants

1. **No hardcoded secrets**: API keys from `os.environ` only
2. **Parameterized queries**: No f-string SQL interpolation
3. **Read-only trading**: acct #886614932 never receives orders
4. **ISO timestamps**: All TIMESTAMPTZ columns use ISO 8601 strings
5. **Single instance**: PID guard prevents duplicate daemon runs
6. **Signal provenance**: evidence_blob required for all scores