# ZO-Sentinel External API — Quick Reference

Read-only API for querying MCP (Model Context Protocol) server trust
assessments produced by ZO-Sentinel.

**Base URL:** `https://<your-zite-hostname>/v1`  *(issued separately)*

**Auth:** send your API key on every request in the `X-API-Key` header.

**Rate limit:** 60 requests per minute per key, sliding window.

**Trial access:** this key expires 2026-04-20T05:30:00Z (36h). Let me know
if you want to extend.

---

## Endpoints

### GET /v1/health
Unauthenticated liveness probe.
```bash
curl https://<host>/v1/health
# -> {"status":"ok","service":"sentinel_external_api","version":"1.0"}
```

### GET /v1/search?q=<text>&limit=<n>
Full-text search across MCP name / url / server_id.
- `q` — required, 2–200 characters, must contain at least 2 non-wildcard chars
- `limit` — optional, default 10, max 50

```bash
curl -H "X-API-Key: $KEY" \
  'https://<host>/v1/search?q=github&limit=5'
```
Returns an array of `{server_id, name, verdict, trust_score}`.

### GET /v1/mcp/{server_id}
Full assessment for one MCP.
- `server_id` — 32-char lowercase hex (MD5). Invalid formats return 400.

```bash
curl -H "X-API-Key: $KEY" \
  'https://<host>/v1/mcp/a1b2c3d4e5f60002...'
```
Returns `{server_id, name, url, verdict, trust_score, verdict_reasoning,
confidence, risk_tier, last_assessed, registry_source}`.

### GET /v1/mcp/{server_id}/threats?limit=<n>
Threat associations for one MCP, newest first.
- `limit` — optional, default 20, max 100

Returns array of `{threat_type, severity, evidence, reported_at}`.
Severity values: `CRITICAL`, `HIGH`, `MEDIUM`, `LOW`.

### GET /v1/mcp/{server_id}/risk
Risk register entry for one MCP.

Returns `{risk_rank, risk_tier, threat_count, staleness_days, computed_at}`.
If the server is registered but has no risk entry, all fields return null.

---

## Verdict taxonomy

The `verdict` field is one of:

| Verdict | Composite score | Meaning |
|---|---|---|
| TRUSTED_GENERAL | >75 | Approved for general enterprise use |
| TRUSTED_RESEARCH | >60 | Safe for research / exploratory use |
| ENTERPRISE_CONTROLLED | >45 | Acceptable with documented controls |
| CAUTION_LIMITED | >30 | Requires additional review |
| HIGH_RISK_ISOLATED | >15 | Sandboxed environments only |
| KNOWN_THREAT | ≤15 | Hardcoded block |
| INSUFFICIENT | n/a | Not enough signal data to verdict |

`trust_score` is the composite on a 0–100 scale. `confidence` is separate,
on a 0–1 scale, and represents how reliable the assessment is given the
number of signals present.

---

## Error shapes

All errors return JSON.

| Status | When | Body |
|---|---|---|
| 400 | Bad input (bad server_id regex, wildcard-only search) | `{"detail":"..."}` |
| 401 | Missing `X-API-Key` header | `{"detail":"Missing X-API-Key header"}` |
| 403 | Invalid / expired key | `{"detail":"Invalid API key"}` |
| 404 | server_id not found in registry | `{"detail":"MCP server not found"}` |
| 429 | Rate limit exceeded | `{"detail":"Rate limit exceeded"}` + Retry-After header |
| 500 | Unhandled server error | `{"error":"...","request_id":"<uuid>"}` (send me the request_id) |
| 503 | Database temporarily unavailable | `{"detail":"Database temporarily unavailable"}` |

---

## Not in v1.0

The API is strictly read-only. There are no write/mutate endpoints; all
MCP assessments are produced by internal ZO-Sentinel daemons. Likewise
no bulk export, no webhooks out, no `/v1/verdicts` listing endpoint,
no attestation or history endpoints yet (coming).

---

## Contact

Robin — send the `request_id` from any 500 response so I can trace it.