# ZO-SENTINEL External API Reference

**Version:** 1.0  
**Base URL:** `http://localhost:8791`  
**Protocol:** REST/JSON  
**Last Updated:** 2024

---

## Appendix A: External API Reference

This document describes the external API for ZO-SENTINEL, providing programmatic access to MCP server safety intelligence.

---

## Authentication

All requests must include a valid API key in the request header:

```
X-API-Key: <your-api-key>
```

API keys are provisioned through the Sentinel administrative interface. Keys are scoped to specific capabilities based on the associated service principal.

**Important:** Never expose API keys in logs, URLs, or version control. Rotate compromised keys immediately through the Sentinel admin console.

---

## Rate Limiting

| Limit Type | Value |
|------------|-------|
| Requests per minute | 60 |
| Per API key | Yes |
| Burst allowance | 10 requests |
| Window | Sliding 60-second |

Rate limit headers are included in every response:

```
X-RateLimit-Limit: 60
X-RateLimit-Remaining: <n>
X-RateLimit-Reset: <unix-timestamp>
```

When limits are exceeded, the API returns `429 Too Many Requests`.

---

## Endpoints

### 1. Search MCP Server by Name

Search the MCP server registry for entries matching a server name or pattern.

**Endpoint:** `GET /api/v1/mcp/search`

**Query Parameters:**

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `name` | string | Yes | Server name or partial name match |
| `limit` | integer | No | Max results (default: 10, max: 100) |
| `offset` | integer | No | Pagination offset (default: 0) |

**Request Example:**
```bash
curl -X GET "http://localhost:8791/api/v1/mcp/search?name=openai&limit=5" \
  -H "X-API-Key: your-api-key"
```

**Response (200 OK):**
```json
{
  "query": "openai",
  "total": 2,
  "results": [
    {
      "server_id": "srv_mcp_openai_001",
      "name": "openai-mcp-server",
      "vendor": "OpenAI",
      "version": "1.2.0",
      "capabilities": ["text-generation", "embedding"],
      "trust_score": 0.87,
      "last_seen": "2024-01-15T10:30:00Z",
      "risk_flags": []
    },
    {
      "server_id": "srv_mcp_openai_assist_002",
      "name": "openai-assistant-mcp",
      "vendor": "OpenAI",
      "version": "0.9.1",
      "capabilities": ["chat", "function-calling"],
      "trust_score": 0.82,
      "last_seen": "2024-01-14T22:15:00Z",
      "risk_flags": ["sandbox-required"]
    }
  ]
}
```

**Referenced by:** `signal_analyser.py` (search operations)

---

### 2. Get Trust Verdict

Retrieve the composite trust verdict for a specific MCP server.

**Endpoint:** `GET /api/v1/mcp/{server_id}/verdict`

**Path Parameters:**

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `server_id` | string | Yes | Unique server identifier |

**Request Example:**
```bash
curl -X GET "http://localhost:8791/api/v1/mcp/srv_mcp_openai_001/verdict" \
  -H "X-API-Key: your-api-key"
```

**Response (200 OK):**
```json
{
  "server_id": "srv_mcp_openai_001",
  "verdict": {
    "level": "trusted",
    "score": 0.87,
    "confidence": "high",
    "factors": [
      {"factor": "vendor-reputation", "weight": 0.4, "score": 0.95},
      {"factor": "attestation-count", "weight": 0.3, "score": 0.90},
      {"factor": "vulnerability-history", "weight": 0.2, "score": 0.75},
      {"factor": "sandbox-compliance", "weight": 0.1, "score": 0.80}
    ],
    "computed_at": "2024-01-15T12:00:00Z"
  },
  "recommendations": [
    "Approved for general use",
    "Monitor for new vulnerability disclosures"
  ],
  "restrictions": []
}
```

**Verdict Levels:**

| Level | Score Range | Meaning |
|-------|-------------|---------|
| `trusted` | 0.75 - 1.00 | Approved for production use |
| `conditional` | 0.50 - 0.74 | Use with safeguards enabled |
| `restricted` | 0.25 - 0.49 | Limited use, enhanced monitoring |
| `blocked` | 0.00 - 0.24 | Do not use in any context |

**Referenced by:** `trust_synthesiser.py` (verdict computation), Sentinel dashboard

---

### 3. Get Signal Summary

Retrieve the aggregated security signals for a specific MCP server.

**Endpoint:** `GET /api/v1/mcp/{server_id}/signals`

**Path Parameters:**

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `server_id` | string | Yes | Unique server identifier |

**Query Parameters:**

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `window_days` | integer | No | Analysis window in days (default: 30, max: 365) |

**Request Example:**
```bash
curl -X GET "http://localhost:8791/api/v1/mcp/srv_mcp_openai_001/signals?window_days=30" \
  -H "X-API-Key: your-api-key"
```

**Response (200 OK):**
```json
{
  "server_id": "srv_mcp_openai_001",
  "analysis_window": {
    "start": "2023-12-16T00:00:00Z",
    "end": "2024-01-15T23:59:59Z",
    "days": 30
  },
  "signals": {
    "threat_intel": {
      "score": 0.90,
      "indicators": [
        {"type": "known-vendor", "source": "otx", "age_hours": 720}
      ]
    },
    "vulnerability": {
      "score": 0.75,
      "cves_detected": 0,
      "last_patched": "2024-01-10T00:00:00Z"
    },
    "behavior": {
      "score": 0.82,
      "anomalies": [],
      "permission_requests": ["network-outbound", "data-read"]
    },
    "reputation": {
      "score": 0.88,
      "community_rating": 4.2,
      "attestation_count": 156
    }
  },
  "composite_score": 0.84,
  "computed_at": "2024-01-15T12:00:00Z"
}
```

**Referenced by:** `signal_analyser.py` (signal aggregation)

---

### 4. Get Attestation Records

Retrieve attestation records proving trust assessment by authorized parties.

**Endpoint:** `GET /api/v1/mcp/{server_id}/attestations`

**Path Parameters:**

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `server_id` | string | Yes | Unique server identifier |

**Query Parameters:**

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `attestor` | string | No | Filter by specific attestor ID |
| `status` | string | No | Filter by attestation status: `valid`, `revoked`, `expired` |
| `limit` | integer | No | Max results (default: 20, max: 200) |

**Request Example:**
```bash
curl -X GET "http://localhost:8791/api/v1/mcp/srv_mcp_openai_001/attestations?status=valid&limit=10" \
  -H "X-API-Key: your-api-key"
```

**Response (200 OK):**
```json
{
  "server_id": "srv_mcp_openai_001",
  "attestations": [
    {
      "attestation_id": "attest_2024_001",
      "attestor": {
        "id": "attestor_zocompanion_001",
        "name": "ZoCompanion",
        "type": "internal-service"
      },
      "claimed_trust_level": "trusted",
      "scope": ["production", "sandbox"],
      "evidence": {
        "code_review_date": "2024-01-05T00:00:00Z",
        "pen_test_date": "2023-12-20T00:00:00Z",
        "compliance_framework": "SOC2-TypeII"
      },
      "attested_at": "2024-01-05T18:30:00Z",
      "expires_at": "2025-01-05T18:30:00Z",
      "status": "valid",
      "signature": "sha256:abc123..."
    },
    {
      "attestation_id": "attest_2023_089",
      "attestor": {
        "id": "attestor_zo_agent_health_001",
        "name": "Sentinel Agent Health",
        "type": "automated-monitor"
      },
      "claimed_trust_level": "trusted",
      "scope": ["production"],
      "evidence": {
        "runtime_verification": true,
        "uptime_percent": 99.95
      },
      "attested_at": "2024-01-10T00:00:00Z",
      "expires_at": "2024-02-10T00:00:00Z",
      "status": "valid",
      "signature": "sha256:def456..."
    }
  ],
  "total": 2
}
```

**Referenced by:** `attestations.py` (attestation management), compliance audit systems

---

## Error Codes

All error responses follow this structure:

```json
{
  "error": {
    "code": "ERROR_CODE",
    "message": "Human-readable description",
    "details": {},
    "request_id": "req_abc123"
  }
}
```

### HTTP Status Codes

| Status | Code | Description | Remediation |
|--------|------|-------------|-------------|
| `400` | `INVALID_REQUEST` | Malformed request or missing parameters | Check request format and parameters |
| `401` | `AUTHENTICATION_REQUIRED` | Missing or invalid API key | Provide valid X-API-Key header |
| `403` | `AUTHORIZATION_FAILED` | Valid key but insufficient permissions | Request additional scopes from admin |
| `404` | `SERVER_NOT_FOUND` | Requested server_id does not exist | Verify server identifier |
| `422` | `VALIDATION_ERROR` | Request valid but failed semantic validation | Review error details field |
| `429` | `RATE_LIMIT_EXCEEDED` | Too many requests | Wait and retry; check X-RateLimit-Reset |
| `500` | `INTERNAL_ERROR` | Server-side failure | Contact Sentinel operations team |
| `503` | `SERVICE_UNAVAILABLE` | API temporarily offline | Monitor status endpoint; retry later |

### Error Response Examples

**401 Unauthorized:**
```json
{
  "error": {
    "code": "AUTHENTICATION_REQUIRED",
    "message": "API key is required for all requests",
    "details": {"header": "X-API-Key"},
    "request_id": "req_7f8a9b"
  }
}
```

**403 Forbidden:**
```json
{
  "error": {
    "code": "AUTHORIZATION_FAILED",
    "message": "API key lacks required scope: mcp:write",
    "details": {"required_scope": "mcp:write", "current_scopes": ["mcp:read"]},
    "request_id": "req_8g9b0c"
  }
}
```

**429 Too Many Requests:**
```json
{
  "error": {
    "code": "RATE_LIMIT_EXCEEDED",
    "message": "Rate limit of 60 requests per minute exceeded",
    "details": {
      "limit": 60,
      "reset_at": "2024-01-15T12:01:00Z",
      "retry_after_seconds": 45
    },
    "request_id": "req_9h0c1d"
  }
}
```

**500 Internal Server Error:**
```json
{
  "error": {
    "code": "INTERNAL_ERROR",
    "message": "An unexpected error occurred processing your request",
    "details": {},
    "request_id": "req_0i1d2e"
  }
}
```

---

## Health Check

**Endpoint:** `GET /health`

Returns API service health status. Does not require authentication.

**Response (200 OK):**
```json
{
  "status": "healthy",
  "version": "1.0.0",
  "uptime_seconds": 86400,
  "dependencies": {
    "write_service": "connected",
    "duckdb": "connected",
    "mesh_memory": "connected"
  },
  "timestamp": "2024-01-15T12:00:00Z"
}
```

---

## SDK Integration

### Python Client Example

```python
import requests

class SentinelAPIClient:
    def __init__(self, api_key: str, base_url: str = "http://localhost:8791"):
        self.api_key = api_key
        self.base_url = base_url
        self.session = requests.Session()
        self.session.headers.update({"X-API-Key": api_key})
    
    def search_mcp(self, name: str, limit: int = 10) -> dict:
        response = self.session.get(
            f"{self.base_url}/api/v1/mcp/search",
            params={"name": name, "limit": limit},
            timeout=30
        )
        response.raise_for_status()
        return response.json()
    
    def get_verdict(self, server_id: str) -> dict:
        response = self.session.get(
            f"{self.base_url}/api/v1/mcp/{server_id}/verdict",
            timeout=30
        )
        response.raise_for_status()
        return response.json()
    
    def get_signals(self, server_id: str, window_days: int = 30) -> dict:
        response = self.session.get(
            f"{self.base_url}/api/v1/mcp/{server_id}/signals",
            params={"window_days": window_days},
            timeout=30
        )
        response.raise_for_status()
        return response.json()
    
    def get_attestations(self, server_id: str, status: str = None) -> dict:
        params = {"status": status} if status else {}
        response = self.session.get(
            f"{self.base_url}/api/v1/mcp/{server_id}/attestations",
            params=params,
            timeout=30
        )
        response.raise_for_status()
        return response.json()
```

---

## Appendix: Referenced Modules

| Module | Purpose | API Role |
|--------|---------|----------|
| `signal_analyser.py` | Analyzes MCP server behavior and aggregates security signals | Provides signal data for `/signals` and search operations |
| `trust_synthesiser.py` | Computes composite trust verdicts from multiple signal sources | Powers verdict computation for `/verdict` endpoint |
| `attestations.py` | Manages trust attestations from authorized parties | Serves attestation records via `/attestations` endpoint |

---

*End of External API Reference*