import logging
from datetime import datetime, timezone
from fastapi import APIRouter, Query, HTTPException
from typing import Optional, List, Dict, Any

import requests

WRITE_SERVICE_URL = 'http://localhost:8772'
QUERY_URL = 'http://localhost:8772/query'
EXECUTE_URL = 'http://localhost:8772/execute'
SERVICE_NAME = 'risk_tier_overview_router'

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(name)s] %(levelname)s: %(message)s',
    handlers=[logging.StreamHandler()]
)
log = logging.getLogger(__name__)

router = APIRouter(prefix='/api/risk-tier-overview', tags=['risk-tier-overview'])


def ws_query(sql: str, params: Optional[List[Any]] = None) -> Dict[str, Any]:
    """Query DuckDB via write_service."""
    payload = {'sql': sql}
    if params:
        payload['params'] = params
    try:
        resp = requests.post(QUERY_URL, json=payload, timeout=30)
        resp.raise_for_status()
        return resp.json()
    except requests.RequestException as e:
        log.error(f'ws_query failed: {e}')
        raise HTTPException(status_code=503, detail='Database unavailable')


def ws_write(table: str, rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Write to DuckDB via write_service."""
    payload = {'table': table, 'rows': rows, 'wait': True}
    try:
        resp = requests.post(WRITE_SERVICE_URL, json=payload, timeout=30)
        resp.raise_for_status()
        return resp.json()
    except requests.RequestException as e:
        log.error(f'ws_write failed: {e}')
        raise HTTPException(status_code=503, detail='Write service unavailable')


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@router.get('/summary')
def get_risk_tier_summary() -> Dict[str, Any]:
    """Get overall risk tier distribution summary."""
    sql = """
    SELECT 
        COALESCE(risk_tier, 'UNKNOWN') as risk_tier,
        COUNT(*) as server_count,
        ROUND(COUNT(*) * 100.0 / NULLIF((SELECT COUNT(*) FROM mcp_server_registry WHERE risk_tier IS NOT NULL), 0), 2) as percentage
    FROM mcp_server_registry
    WHERE risk_tier IS NOT NULL
    GROUP BY risk_tier
    ORDER BY 
        CASE risk_tier 
            WHEN 'CRITICAL' THEN 1 
            WHEN 'HIGH' THEN 2 
            WHEN 'MEDIUM' THEN 3 
            WHEN 'LOW' THEN 4 
            WHEN 'INFO' THEN 5 
            ELSE 6 
        END
    """
    result = ws_query(sql)
    rows = result.get('rows', [])
    return {
        'timestamp': utc_now_iso(),
        'total_servers': sum(r.get('server_count', 0) for r in rows),
        'distribution': rows
    }


@router.get('/details')
def get_risk_tier_details(
    risk_tier: Optional[str] = Query(None, description='Filter by specific risk tier'),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0)
) -> Dict[str, Any]:
    """Get detailed server list per risk tier."""
    where_clause = ''
    params: List[Any] = []
    if risk_tier:
        where_clause = 'WHERE risk_tier = ?'
        params.append(risk_tier)
    
    count_sql = f"""
    SELECT COUNT(*) as total
    FROM mcp_server_registry
    {where_clause}
    """
    count_result = ws_query(count_sql, params if risk_tier else None)
    total = count_result.get('rows', [{}])[0].get('total', 0) if count_result.get('rows') else 0
    
    sql = f"""
    SELECT 
        server_id,
        name,
        risk_tier,
        verdict,
        trust_score,
        scan_count,
        last_seen
    FROM mcp_server_registry
    {where_clause}
    ORDER BY 
        CASE risk_tier 
            WHEN 'CRITICAL' THEN 1 
            WHEN 'HIGH' THEN 2 
            WHEN 'MEDIUM' THEN 3 
            WHEN 'LOW' THEN 4 
            WHEN 'INFO' THEN 5 
            ELSE 6 
        END,
        trust_score ASC
    LIMIT ? OFFSET ?
    """
    params.extend([limit, offset])
    result = ws_query(sql, params)
    
    return {
        'timestamp': utc_now_iso(),
        'total': total,
        'limit': limit,
        'offset': offset,
        'servers': result.get('rows', [])
    }


@router.get('/trends')
def get_risk_tier_trends(
    days: int = Query(30, ge=1, le=365, description='Number of days to look back')
) -> Dict[str, Any]:
    """Get risk tier distribution trends over time."""
    sql = """
    SELECT 
        DATE_TRUNC('day', last_seen) as date,
        risk_tier,
        COUNT(*) as server_count
    FROM mcp_server_registry
    WHERE last_seen >= DATE_SUB(CURRENT_TIMESTAMP, INTERVAL ? DAY)
        AND risk_tier IS NOT NULL
    GROUP BY DATE_TRUNC('day', last_seen), risk_tier
    ORDER BY date DESC, risk_tier
    """
    result = ws_query(sql, [days])
    
    return {
        'timestamp': utc_now_iso(),
        'days': days,
        'trends': result.get('rows', [])
    }


@router.get('/statistics')
def get_risk_tier_statistics() -> Dict[str, Any]:
    """Get statistical summary of risk tiers."""
    sql = """
    WITH tier_stats AS (
        SELECT 
            risk_tier,
            COUNT(*) as server_count,
            AVG(trust_score) as avg_trust_score,
            MIN(trust_score) as min_trust_score,
            MAX(trust_score) as max_trust_score,
            STDDEV(trust_score) as stddev_trust_score
        FROM mcp_server_registry
        WHERE risk_tier IS NOT NULL
        GROUP BY risk_tier
    )
    SELECT 
        risk_tier,
        server_count,
        ROUND(avg_trust_score, 2) as avg_trust_score,
        ROUND(min_trust_score, 2) as min_trust_score,
        ROUND(max_trust_score, 2) as max_trust_score,
        ROUND(stddev_trust_score, 2) as stddev_trust_score
    FROM tier_stats
    ORDER BY 
        CASE risk_tier 
            WHEN 'CRITICAL' THEN 1 
            WHEN 'HIGH' THEN 2 
            WHEN 'MEDIUM' THEN 3 
            WHEN 'LOW' THEN 4 
            WHEN 'INFO' THEN 5 
            ELSE 6 
        END
    """
    result = ws_query(sql)
    
    return {
        'timestamp': utc_now_iso(),
        'statistics': result.get('rows', [])
    }


@router.get('/high-risk-servers')
def get_high_risk_servers(
    limit: int = Query(50, ge=1, le=200)
) -> Dict[str, Any]:
    """Get servers in HIGH or CRITICAL risk tiers."""
    sql = """
    SELECT 
        server_id,
        name,
        url,
        risk_tier,
        verdict,
        trust_score,
        last_seen,
        scan_count
    FROM mcp_server_registry
    WHERE risk_tier IN ('HIGH', 'CRITICAL')
    ORDER BY 
        CASE risk_tier WHEN 'CRITICAL' THEN 1 ELSE 2 END,
        trust_score ASC
    LIMIT ?
    """
    result = ws_query(sql, [limit])
    
    return {
        'timestamp': utc_now_iso(),
        'count': len(result.get('rows', [])),
        'servers': result.get('rows', [])
    }


@router.get('/health')
def health() -> Dict[str, str]:
    """Health check endpoint."""
    try:
        ws_query('SELECT 1 as health')
        return {'status': 'ok', 'service': SERVICE_NAME, 'timestamp': utc_now_iso()}
    except Exception as e:
        log.error(f'Health check failed: {e}')
        return {'status': 'degraded', 'service': SERVICE_NAME, 'timestamp': utc_now_iso(), 'error': str(e)}


if __name__ == '__main__':
    import uvicorn
    from fastapi import FastAPI
    
    app = FastAPI()
    app.include_router(router)
    
    PORT = 8785
    log.info(f'Starting {SERVICE_NAME} on port {PORT}')
    uvicorn.run(app, host='0.0.0.0', port=PORT)