# ZO-Sentinel External API Reference

**Version:** 1.0  
**Port:** 8791  
**Base URL:** `http://localhost:8791`  
**Protocol:** REST/JSON  
**Read-Only:** Yes (per PRODUCT_SPEC §7)

---

## Overview

The Sentinel External API provides read-only access to MCP server intelligence, including trust verdicts, signal scores, attestation records, and threat associations. This API is designed for external consumers such as gateways, portals, and compliance tools.

**Note:** All endpoints are read-only. There are no write, create, or update endpoints on this port.

---

## 1. Authentication

All requests must include a valid API key in the `X-API-Key` header.

```
X-API-Key: <your-api-key>
```

### Rate Limits

| Parameter | Value |
|-----------|-------|
| Requests per minute | 60 |
| Per API key | Yes |
| Window | Sliding 60-second |

### Rate Limit Response Headers

Every response includes rate limit information:

```
X-RateLimit-Limit: 60
X-RateLimit-Remaining: <n>
X-RateLimit-Reset: <unix-timestamp>
```

---

## 2. Endpoints

### 2.1 List MCP Servers

Retrieve a paginated list of MCP servers with optional filtering.

**Endpoint:** `GET /servers`

**Query Parameters:**

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `verdict` | string | No | Filter by verdict level (e.g., `TRUSTED_GENERAL`, `HIGH_RISK_ISOLATED`) |
| `registry` | string | No | Filter by registry source (e.g., `github`, `npm`) |
| `limit` | integer | No | Max results per page (default: 20, max: 100) |
| `offset` | integer | No | Pagination offset (default: 0) |

**Response Shape:**

```json
{
  "servers": [
    {
      "server_id": "abc123def456...",
      "name": "string",
      "registry_source": "string",
      "verdict": "string",
      "trust_score": 0.0,
      "last_assessed": "2024-01-15T12:00:00Z"
    }
  ],
  "total": 0,
  "limit": 20,
  "offset": 0
}
```

**Null Handling:** Missing fields are returned as `null`. For example, a server without a verdict would have `"verdict": null`.

**curl Example:**

```bash
# List all servers
curl -X GET "http://localhost:8791/servers" \
  -H "X-API-Key: your-api-key"

# Filter by verdict
curl -X GET "http://localhost:8791/servers?verdict=TRUSTED_GENERAL&limit=10" \
  -H "X-API-Key: your-api-key"

# Paginate results
curl -X GET "http://localhost:8791/servers?limit=20&offset=40" \
  -H "X-API-Key: your-api-key"
```

---

### 2.2 Get Server Details

Retrieve the full record for a specific MCP server, including signal scores and enrichment data.

**Endpoint:** `GET /servers/{server_id}`

**Path Parameters:**

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `server_id` | string | Yes | 32-character MD5 hash identifier |

**Response Shape:**

```json
{
  "server_id": "abc123def456...",
  "name": "string",
  "url": "string",
  "verdict": "string",
  "trust_score": 0.0,
  "verdict_reasoning": "string",
  "confidence": 0.0,
  "risk_tier": "RISK_TIER_1",
  "last_assessed": "2024-01-15T12:00:00Z",
  "registry_source": "string"
}
```

**Null Handling:** All optional fields return `null` if not populated. For example: `"verdict_reasoning": null`.

**curl Example:**

```bash
curl -X GET "http://localhost:8791/servers/abc123def456789012345678901234" \
  -H "X-API-Key: your-api-key"
```

---

### 2.3 Get Attestation Record

Retrieve the attestation text and expiry information for a specific server.

**Endpoint:** `GET /servers/{server_id}/attestation`

**Path Parameters:**

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `server_id` | string | Yes | 32-character MD5 hash identifier |

**Response Shape:**

```json
{
  "server_id": "abc123def456...",
  "attestation_text": "string",
  "attested_at": "2024-01-15T12:00:00Z",
  "expires_at": "2025-01-15T12:00:00Z",
  "attestor": "string",
  "status": "valid"
}
```

**Null Handling:** If no attestation exists, all fields return `null`.

**curl Example:**

```bash
curl -X GET "http://localhost:8791/servers/abc123def456789012345678901234/attestation" \
  -H "X-API-Key: your-api-key"
```

---

### 2.4 Get Threat Associations

Retrieve threat associations for a specific server, if any exist.

**Endpoint:** `GET /servers/{server_id}/threats`

**Path Parameters:**

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `server_id` | string | Yes | 32-character MD5 hash identifier |

**Response Shape:**

```json
{
  "server_id": "abc123def456...",
  "threats": [
    {
      "threat_type": "string",
      "severity": "string",
      "evidence": "string",
      "reported_at": "2024-01-15T12:00:00Z"
    }
  ],
  "threat_count": 0
}
```

**Null Handling:** If no threats exist, `threats` is an empty array `[]` and `threat_count` is `0`.

**curl Example:**

```bash
curl -X GET "http://localhost:8791/servers/abc123def456789012345678901234/threats" \
  -H "X-API-Key: your-api-key"
```

---

### 2.5 Get Aggregated Verdicts

Retrieve aggregated verdict counts across all MCP servers.

**Endpoint:** `GET /verdicts`

**Response Shape:**

```json
{
  "verdicts": {
    "TRUSTED_GENERAL": 150,
    "TRUSTED_RESEARCH": 75,
    "ENTERPRISE_CONTROLLED": 30,
    "CAUTION_LIMITED": 45,
    "HIGH_RISK_ISOLATED": 12,
    "KNOWN_THREAT": 3,
    "INSUFFICIENT": 25
  },
  "total_servers": 340,
  "computed_at": "2024-01-15T12:00:00Z"
}
```

**curl Example:**

```bash
curl -X GET "http://localhost:8791/verdicts" \
  -H "X-API-Key: your-api-key"
```

---

### 2.6 Get Service Health

Retrieve the current health status of the Sentinel service.

**Endpoint:** `GET /health`

**Response Shape:**

```json
{
  "status": "healthy",
  "service": "sentinel_external_api",
  "version": "1.0.0",
  "timestamp": "2024-01-15T12:00:00Z",
  "dependencies": {
    "write_service": "connected",
    "signal_store": "connected"
  }
}
```

**Note:** This endpoint does not require authentication.

**curl Example:**

```bash
curl -X GET "http://localhost:8791/health"
```

---

## 3. Response Schema Reference

### Server Object

| Field | Type | Description | Nullable |
|-------|------|-------------|----------|
| `server_id` | string | 32-char MD5 hash | No |
| `name` | string | Display name | Yes |
| `url` | string | Registry URL | Yes |
| `verdict` | string | Verdict level | Yes |
| `trust_score` | float | Score 0-100 | Yes |
| `verdict_reasoning` | string | Explanation | Yes |
| `confidence` | float | Confidence 0-1 | Yes |
| `risk_tier` | string | RISK_TIER_1 through RISK_TIER_5 | Yes |
| `last_assessed` | datetime | ISO 8601 timestamp | Yes |
| `registry_source` | string | Source registry name | Yes |

### Attestation Object

| Field | Type | Description | Nullable |
|-------|------|-------------|----------|
| `attestation_text` | string | Attestation content | Yes |
| `attested_at` | datetime | When attested | Yes |
| `expires_at` | datetime | Expiry date | Yes |
| `attestor` | string | Attesting party | Yes |
| `status` | string | valid, expired, revoked | Yes |

### Threat Object

| Field | Type | Description | Nullable |
|-------|------|-------------|----------|
| `threat_type` | string | Type classification | No |
| `severity` | string | low, medium, high, critical | No |
| `evidence` | string | Evidence description | Yes |
| `reported_at` | datetime | When reported | Yes |

---

## 4. Error Codes

All error responses follow this structure:

```json
{
  "error": "ERROR_CODE",
  "detail": "Human-readable description",
  "request_id": "req_abc123"
}
```

### HTTP Status Codes

| Status | Error Code | Description |
|--------|------------|-------------|
| 401 | `AUTHENTICATION_REQUIRED` | Missing or invalid API key |
| 404 | `SERVER_NOT_FOUND` | Requested server_id does not exist |
| 429 | `RATE_LIMIT_EXCEEDED` | Rate limit exceeded (60 req/min) |
| 500 | `INTERNAL_ERROR` | Internal server error |

### Error Response Examples

**401 Unauthorized:**

```json
{
  "error": "AUTHENTICATION_REQUIRED",
  "detail": "API key is required for all requests",
  "request_id": "req_7f8a9b"
}
```

**404 Not Found:**

```json
{
  "error": "SERVER_NOT_FOUND",
  "detail": "Server with ID 'abc123def456...' not found",
  "request_id": "req_8g9b0c"
}
```

**429 Too Many Requests:**

```json
{
  "error": "RATE_LIMIT_EXCEEDED",
  "detail": "Rate limit of 60 requests per minute exceeded",
  "request_id": "req_9h0c1d"
}
```

**500 Internal Server Error:**

```json
{
  "error": "INTERNAL_ERROR",
  "detail": "An unexpected error occurred processing your request",
  "request_id": "req_0i1d2e"
}
```

---

## 5. Complete curl Examples

### List Servers

```bash
curl -X GET "http://localhost:8791/servers" \
  -H "X-API-Key: your-api-key"
```

### List Servers with Filters

```bash
curl -X GET "http://localhost:8791/servers?verdict=TRUSTED_GENERAL&registry=github&limit=10&offset=20" \
  -H "X-API-Key: your-api-key"
```

### Get Server Details

```bash
curl -X GET "http://localhost:8791/servers/abc123def456789012345678901234" \
  -H "X-API-Key: your-api-key"
```

### Get Attestation

```bash
curl -X GET "http://localhost:8791/servers/abc123def456789012345678901234/attestation" \
  -H "X-API-Key: your-api-key"
```

### Get Threats

```bash
curl -X GET "http://localhost:8791/servers/abc123def456789012345678901234/threats" \
  -H "X-API-Key: your-api-key"
```

### Get Verdicts

```bash
curl -X GET "http://localhost:8791/verdicts" \
  -H "X-API-Key: your-api-key"
```

### Get Health (No Auth Required)

```bash
curl -X GET "http://localhost:8791/health"
```

---

## 6. Verdict Levels Reference

| Verdict | Description |
|---------|-------------|
| `TRUSTED_GENERAL` | Approved for general production use |
| `TRUSTED_RESEARCH` | Approved for research/development use |
| `ENTERPRISE_CONTROLLED` | Enterprise-managed, additional controls |
| `CAUTION_LIMITED` | Use with caution, limited scope |
| `HIGH_RISK_ISOLATED` | High risk, requires isolation |
| `KNOWN_THREAT` | Confirmed threat, do not use |
| `INSUFFICIENT` | Not enough data for assessment |

---

*End of External API Reference*