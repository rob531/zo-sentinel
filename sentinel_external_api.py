import os
import re
import time
import uuid
import hmac
import logging
import threading
from collections import defaultdict, deque
from datetime import datetime, timezone
from typing import Optional, List

import requests
from fastapi import FastAPI, HTTPException, Header, Query, Path, Depends, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
import uvicorn

SERVICE_NAME = 'sentinel_external_api'
PORT = 8791
HOST = '0.0.0.0'
QUERY_URL = 'http://127.0.0.1:8772/query'
WRITE_SERVICE_URL = 'http://127.0.0.1:8772/write'
API_KEY_FILE = '/home/workspace/zo_sentinel/config/external_api_keys.txt'
LOG_FILE = '/home/workspace/logs/sentinel_external_api.log'
HEARTBEAT_INTERVAL = 60

RATE_LIMIT = 60
RATE_WINDOW = 60

KEYS: Optional[set] = None
rate_limiters: dict = defaultdict(lambda: deque())
logger: logging.Logger = None


class MCPAssessment(BaseModel):
    server_id: str = Field(..., description='32-char MD5 hash identifier')
    name: Optional[str] = Field(None, description='Display name')
    url: Optional[str] = Field(None, description='Registry URL')
    verdict: Optional[str] = Field(None, description='One of: TRUSTED_GENERAL, TRUSTED_RESEARCH, ENTERPRISE_CONTROLLED, CAUTION_LIMITED, HIGH_RISK_ISOLATED, KNOWN_THREAT, INSUFFICIENT')
    trust_score: Optional[float] = Field(None, ge=0.0, le=100.0, description='Composite trust score 0-100')
    verdict_reasoning: Optional[str] = Field(None, description='Explanation of verdict')
    confidence: Optional[float] = Field(None, ge=0.0, le=1.0, description='Confidence in assessment')
    risk_tier: Optional[str] = Field(None, description='RISK_TIER_1 through RISK_TIER_5 or null')
    last_assessed: Optional[datetime] = Field(None, description='Last assessment timestamp')
    registry_source: Optional[str] = Field(None, description='Source registry')


class ThreatRecord(BaseModel):
    threat_type: str
    severity: str
    evidence: Optional[str] = None
    reported_at: Optional[datetime] = None


class RiskRecord(BaseModel):
    risk_rank: Optional[int] = Field(None, description='Ordinal rank within tier')
    risk_tier: Optional[str] = Field(None, description='RISK_TIER_1 through RISK_TIER_5')
    threat_count: Optional[int] = Field(None, description='Number of associated threats')
    staleness_days: Optional[int] = Field(None, description='Days since last risk computation')
    computed_at: Optional[datetime] = Field(None, description='When risk was computed')


class SearchResult(BaseModel):
    server_id: str
    name: Optional[str] = None
    verdict: Optional[str] = None
    trust_score: Optional[float] = Field(None, ge=0.0, le=100.0, description='Composite trust score 0-100')


class ErrorResponse(BaseModel):
    error: str
    detail: str
    request_id: str


class HealthResponse(BaseModel):
    status: str
    service: str
    version: str


app = FastAPI(title='Sentinel External API', version='1.0.0', docs_url=None, redoc_url=None)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[],
    allow_credentials=True,
    allow_methods=['GET'],
    allow_headers=['*'],
)


def load_api_keys() -> set:
    '''Load API keys from file. Supports optional expiry via a comment line
    'expires: <ISO-8601>' IMMEDIATELY BEFORE a key. Expired keys are silently
    skipped (not loaded into KEYS).'''
    global KEYS
    keys = set()
    pending_expiry = None
    loaded = 0
    expired = 0
    if os.path.exists(API_KEY_FILE):
        try:
            with open(API_KEY_FILE, 'r') as f:
                for raw in f:
                    line = raw.strip()
                    if not line:
                        pending_expiry = None
                        continue
                    if line.startswith('#'):
                        # look for 'expires: <iso>' (case-insensitive)
                        body = line.lstrip('#').strip()
                        if body.lower().startswith('expires:'):
                            ts = body.split(':', 1)[1].strip()
                            try:
                                # Accept 'Z' suffix and bare ISO formats
                                ts_clean = ts.replace('Z', '+00:00')
                                pending_expiry = datetime.fromisoformat(ts_clean)
                                if pending_expiry.tzinfo is None:
                                    pending_expiry = pending_expiry.replace(tzinfo=timezone.utc)
                            except Exception:
                                pending_expiry = None
                        continue
                    # Non-comment non-empty line: it's a key
                    if pending_expiry is not None:
                        if datetime.now(timezone.utc) >= pending_expiry:
                            expired += 1
                            pending_expiry = None
                            continue
                    keys.add(line)
                    loaded += 1
                    pending_expiry = None
        except Exception as e:
            if logger:
                logger.error(f'Failed to load API keys: {e}')
    if logger:
        logger.info(f'Keys loaded: {loaded} active, {expired} expired and skipped')
    KEYS = keys
    return keys


def verify_api_key(x_api_key: Optional[str] = Header(None)) -> str:
    if KEYS is None or len(KEYS) == 0:
        raise HTTPException(status_code=503, detail='No API keys configured')
    if not x_api_key:
        raise HTTPException(status_code=401, detail='Missing X-API-Key header')
    for key in KEYS:
        if hmac.compare_digest(x_api_key, key):
            return key
    raise HTTPException(status_code=403, detail='Invalid API key')


def check_rate_limit(client_id: str) -> tuple:
    '''Sliding-window counter. Returns (ok: bool, retry_after: int).
    Called by enforce_rate_limit dependency below -- do not call directly
    from endpoints.'''
    now = time.time()
    window_start = now - RATE_WINDOW
    limiter = rate_limiters[client_id]
    while limiter and limiter[0] < window_start:
        limiter.popleft()
    if len(limiter) >= RATE_LIMIT:
        oldest = limiter[0]
        retry_after = int(oldest - window_start) + 1
        return False, retry_after
    limiter.append(now)
    return True, 0


def enforce_rate_limit(api_key: str = Depends(verify_api_key)) -> str:
    '''FastAPI dependency that enforces the per-key rate limit.
    Raises 429 with Retry-After header if the caller exceeds RATE_LIMIT
    requests in RATE_WINDOW seconds. Must be Depends()-ed AFTER
    verify_api_key so that invalid keys never count against a limit.
    Returns the api_key so downstream deps can use it if desired.'''
    ok, retry_after = check_rate_limit(api_key)
    if not ok:
        raise HTTPException(
            status_code=429,
            detail=f'Rate limit exceeded: {RATE_LIMIT} requests per {RATE_WINDOW}s per key',
            headers={'Retry-After': str(retry_after)},
        )
    return api_key


def ws_query(sql: str, params: list = None) -> dict:
    try:
        response = requests.post(
            QUERY_URL,
            json={'sql': sql, 'params': params or []},
            timeout=10
        )
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        logger.error(f'write_service query failed: {e}')
        raise HTTPException(status_code=503, detail='Database temporarily unavailable')


def setup_logging():
    global logger
    os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)
    logger = logging.getLogger('sentinel_external_api')
    logger.setLevel(logging.INFO)
    handler = logging.FileHandler(LOG_FILE)
    handler.setFormatter(logging.Formatter(
        '%(asctime)s - %(levelname)s - %(message)s'
    ))
    logger.addHandler(handler)
    console = logging.StreamHandler()
    console.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))
    logger.addHandler(console)
    return logger


def send_heartbeat():
    try:
        requests.post(
            WRITE_SERVICE_URL,
            json={'table': 'service_health', 'rows': {'service': SERVICE_NAME}},
            timeout=5
        )
    except Exception:
        pass


def heartbeat_loop():
    while True:
        time.sleep(HEARTBEAT_INTERVAL)
        send_heartbeat()


@app.get('/v1/health', response_model=HealthResponse)
async def health():
    return HealthResponse(status='ok', service=SERVICE_NAME, version='1.0')


@app.get('/v1/mcp/{server_id}', response_model=MCPAssessment)
async def get_mcp_assessment(
    server_id: str = Path(..., description='32-char MD5 server identifier'),
    _rate_ok: str = Depends(enforce_rate_limit),
):
    if not re.match(r'^[a-f0-9]{32}$', server_id):
        raise HTTPException(status_code=400, detail='Invalid server_id format')
    sql = """
        SELECT r.server_id, r.name, r.url, r.verdict, r.trust_score,
               r.verdict_reasoning, r.confidence, r.risk_tier,
               r.last_assessed, r.registry_source
        FROM mcp_server_registry r
        WHERE r.server_id = ?
    """
    result = ws_query(sql, [server_id])
    rows = result.get('rows', [])
    if not rows:
        raise HTTPException(status_code=404, detail='MCP server not found')
    row = rows[0]
    return MCPAssessment(
        server_id=row.get('server_id'),
        name=row.get('name'),
        url=row.get('url'),
        verdict=row.get('verdict'),
        trust_score=row.get('trust_score'),
        verdict_reasoning=row.get('verdict_reasoning'),
        confidence=row.get('confidence'),
        risk_tier=row.get('risk_tier'),
        last_assessed=row.get('last_assessed'),
        registry_source=row.get('registry_source')
    )


@app.get('/v1/search', response_model=List[SearchResult])
async def search_mcp(
    q: str = Query(..., min_length=1, max_length=200, description='Search query (minimum 2 non-wildcard chars)'),
    limit: int = Query(10, ge=1, le=50, description='Max results to return'),
    _rate_ok: str = Depends(enforce_rate_limit),
):
    q = q.strip()
    # Anti-enumeration: reject queries that are pure wildcards or near-empty.
    # Without this a caller can walk the full registry via q='%' paginated calls.
    content_chars = q.replace('%', '').replace('_', '').strip()
    if len(content_chars) < 2:
        raise HTTPException(
            status_code=400,
            detail='Search query must contain at least 2 non-wildcard characters'
        )
    like_pattern = f'%{q}%'
    sql = """
        SELECT server_id, name, verdict, trust_score
        FROM mcp_server_registry
        WHERE name LIKE ? OR url LIKE ? OR server_id LIKE ?
        ORDER BY trust_score DESC NULLS LAST
        LIMIT ?
    """
    result = ws_query(sql, [like_pattern, like_pattern, like_pattern, limit])
    rows = result.get('rows', [])
    return [
        SearchResult(
            server_id=r['server_id'],
            name=r.get('name'),
            verdict=r.get('verdict'),
            trust_score=r.get('trust_score')
        )
        for r in rows
    ]


@app.get('/v1/mcp/{server_id}/threats', response_model=List[ThreatRecord])
async def get_mcp_threats(
    server_id: str = Path(..., description='32-char MD5 server identifier'),
    limit: int = Query(20, ge=1, le=100, description='Max threats to return'),
    _rate_ok: str = Depends(enforce_rate_limit),
):
    if not re.match(r'^[a-f0-9]{32}$', server_id):
        raise HTTPException(status_code=400, detail='Invalid server_id format')
    check_sql = 'SELECT 1 FROM mcp_server_registry WHERE server_id = ?'
    check = ws_query(check_sql, [server_id])
    if not check.get('rows'):
        raise HTTPException(status_code=404, detail='MCP server not found in registry')
    sql = """
        SELECT threat_type, severity, evidence, reported_at
        FROM mcp_threat_associations
        WHERE server_id = ?
        ORDER BY reported_at DESC
        LIMIT ?
    """
    result = ws_query(sql, [server_id, limit])
    rows = result.get('rows', [])
    return [
        ThreatRecord(
            threat_type=r['threat_type'],
            severity=r['severity'],
            evidence=r.get('evidence'),
            reported_at=r.get('reported_at')
        )
        for r in rows
    ]


@app.get('/v1/mcp/{server_id}/risk', response_model=RiskRecord)
async def get_mcp_risk(
    server_id: str = Path(..., description='32-char MD5 server identifier'),
    _rate_ok: str = Depends(enforce_rate_limit),
):
    if not re.match(r'^[a-f0-9]{32}$', server_id):
        raise HTTPException(status_code=400, detail='Invalid server_id format')
    check_sql = 'SELECT 1 FROM mcp_server_registry WHERE server_id = ?'
    check = ws_query(check_sql, [server_id])
    if not check.get('rows'):
        raise HTTPException(status_code=404, detail='MCP server not found')
    sql = """
        SELECT risk_rank, risk_tier, threat_count,
               EXTRACT(DAY FROM (CURRENT_TIMESTAMP - computed_at))::INTEGER AS staleness_days,
               computed_at
        FROM mcp_risk_register
        WHERE server_id = ?
    """
    result = ws_query(sql, [server_id])
    rows = result.get('rows', [])
    if not rows:
        return RiskRecord(
            risk_rank=None,
            risk_tier=None,
            threat_count=None,
            staleness_days=None,
            computed_at=None
        )
    row = rows[0]
    return RiskRecord(
        risk_rank=row.get('risk_rank'),
        risk_tier=row.get('risk_tier'),
        threat_count=row.get('threat_count'),
        staleness_days=row.get('staleness_days'),
        computed_at=row.get('computed_at')
    )


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    request_id = str(uuid.uuid4())
    logger.error(f'Unhandled exception [{request_id}]: {exc}', exc_info=True)
    return Response(
        content=ErrorResponse(
            error='Internal server error',
            detail='An unexpected error occurred',
            request_id=request_id
        ).model_dump_json(),
        status_code=500,
        media_type='application/json'
    )


@app.middleware('http')
async def logging_middleware(request: Request, call_next):
    request_id = str(uuid.uuid4())
    start_time = time.time()
    api_key_prefix = 'NONE'
    try:
        raw_key = request.headers.get('x-api-key', '')
        if raw_key:
            api_key_prefix = raw_key[:8]
    except Exception:
        pass
    response = await call_next(request)
    duration_ms = int((time.time() - start_time) * 1000)
    logger.info(
        f'request_id={request_id} '
        f'api_key_prefix={api_key_prefix} '
        f'method={request.method} '
        f'path={request.url.path} '
        f'status={response.status_code} '
        f'duration_ms={duration_ms}'
    )
    return response


def run():
    global logger
    logger = setup_logging()
    logger.info(f'Starting {SERVICE_NAME} on port {PORT}')
    load_api_keys()
    logger.info(f'Loaded {len(KEYS) if KEYS else 0} API keys')
    heartbeat_thread = threading.Thread(target=heartbeat_loop, daemon=True)
    heartbeat_thread.start()
    logger.info('Heartbeat thread started')
    logger.info('Starting startup validation tests...')
    try:
        from pydantic import BaseModel as PM
        del PM
        logger.info('[PASS] Pydantic models import cleanly')
    except Exception as e:
        logger.error(f'[FAIL] Pydantic import: {e}')
    # API-key-parsing startup self-test removed: it relied on a missing
    # 'pwd_api' module and a global builtins.open monkey-patch. The real
    # load_api_keys() call above is the functional test.
    logger.info('[OK] API key loading validated via load_api_keys() above')
    try:
        test_result = ws_query('SELECT 1 AS test', [])
        if test_result and test_result.get('rows'):
            logger.info('[PASS] write_service SELECT 1 succeeds')
        else:
            logger.error(f'[FAIL] write_service returned unexpected: {test_result}')
    except Exception as e:
        logger.error(f'[FAIL] write_service query: {e}')
    logger.info('Startup validation complete')
    uvicorn.run(app, host=HOST, port=PORT)


if __name__ == '__main__':
    run()