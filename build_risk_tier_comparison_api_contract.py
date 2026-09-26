import logging
import os
import sys
import time
from datetime import datetime, timezone
from fastapi import FastAPI, HTTPException, Query
from typing import Optional, List, Dict, Any
import uvicorn

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(name)s] %(levelname)s: %(message)s',
    filename='/home/workspace/logs/risk_tier_comparison_api.log'
)
logger = logging.getLogger(__name__)

SERVICE_NAME = 'risk_tier_comparison_api'
PORT = 8786
WRITE_SERVICE_URL = 'http://localhost:8772'
PID_FILE = '/tmp/risk_tier_comparison_api.pid'

app = FastAPI(title='Risk Tier Comparison API')


def ws_query(sql: str) -> List[Dict[str, Any]]:
    """Query write_service for data."""
    try:
        import requests
        response = requests.post(
            f'{WRITE_SERVICE_URL}/query',
            json={'sql': sql},
            timeout=30
        )
        response.raise_for_status()
        data = response.json()
        return data.get('rows', [])
    except Exception as e:
        logger.error(f'ws_query failed: {e}')
        return []


def ws_write(table: str, rows: List[Dict[str, Any]]) -> bool:
    """Write to write_service."""
    try:
        import requests
        response = requests.post(
            f'{WRITE_SERVICE_URL}/write',
            json={'table': table, 'rows': rows, 'wait': True},
            timeout=30
        )
        response.raise_for_status()
        return True
    except Exception as e:
        logger.error(f'ws_write failed: {e}')
        return False


def check_single_instance() -> bool:
    """Ensure only one instance runs."""
    import os
    if os.path.exists(PID_FILE):
        with open(PID_FILE, 'r') as f:
            old_pid = f.read().strip()
        try:
            import os as os_module
            os_module.kill(int(old_pid), 0)
            logger.error(f'Instance already running with PID {old_pid}')
            return False
        except (OSError, ValueError):
            pass
    with open(PID_FILE, 'w') as f:
        f.write(str(os.getpid()))
    return True


def remove_pid_file():
    """Remove PID file on exit."""
    import os
    try:
        os.remove(PID_FILE)
    except OSError:
        pass


def signal_handler(signum, frame):
    """Handle shutdown signals."""
    logger.info(f'Received signal {signum}, shutting down')
    remove_pid_file()
    sys.exit(0)


def send_heartbeat():
    """Send service heartbeat."""
    ts = datetime.now(timezone.utc).isoformat()
    ws_write('service_health', [{
        'service': SERVICE_NAME,
        'last_heartbeat': ts,
        'status': 'ok'
    }])


@app.get('/health')
def health():
    """Health check endpoint."""
    return {'status': 'ok', 'service': SERVICE_NAME, 'ts': datetime.now(timezone.utc).isoformat()}


@app.get('/api/v1/risk-tier-comparison')
def get_risk_tier_comparison(
    include_servers: bool = Query(False, description='Include server list per tier'),
    min_risk_rank: Optional[int] = Query(None, ge=1, le=100),
    max_risk_rank: Optional[int] = Query(None, ge=1, le=100)
):
    """
    Get comprehensive comparison of all risk tiers.
    
    Returns distribution, statistics, and optionally server lists per tier.
    """
    tier_filter = ''
    if min_risk_rank is not None:
        tier_filter += f" AND risk_rank >= {min_risk_rank}"
    if max_risk_rank is not None:
        tier_filter += f" AND risk_rank <= {max_risk_rank}"
    
    tier_sql = f"""
        SELECT 
            risk_tier,
            COUNT(*) as server_count,
            AVG(risk_rank) as avg_risk_rank,
            MIN(risk_rank) as min_risk_rank,
            MAX(risk_rank) as max_risk_rank,
            MAX(computed_at) as last_computed
        FROM mcp_risk_register
        WHERE 1=1{tier_filter}
        GROUP BY risk_tier
        ORDER BY avg_risk_rank DESC
    """
    tier_data = ws_query(tier_sql)
    
    verdict_sql = """
        SELECT 
            r.risk_tier,
            s.verdict,
            COUNT(*) as count
        FROM mcp_risk_register r
        JOIN mcp_server_registry s ON r.server_id = s.server_id
        GROUP BY r.risk_tier, s.verdict
        ORDER BY r.risk_tier, count DESC
    """
    verdict_dist = ws_query(verdict_sql)
    
    verdict_by_tier: Dict[str, Dict[str, int]] = {}
    for row in verdict_dist:
        tier = row.get('risk_tier', 'UNKNOWN')
        if tier not in verdict_by_tier:
            verdict_by_tier[tier] = {}
        verdict_by_tier[tier][row.get('verdict', 'UNKNOWN')] = row.get('count', 0)
    
    result = {
        'tiers': [],
        'total_servers': sum(t.get('server_count', 0) for t in tier_data),
        'ts': datetime.now(timezone.utc).isoformat()
    }
    
    for tier_row in tier_data:
        tier_name = tier_row.get('risk_tier', 'UNKNOWN')
        tier_entry = {
            'tier': tier_name,
            'server_count': tier_row.get('server_count', 0),
            'avg_risk_rank': round(tier_row.get('avg_risk_rank', 0), 2),
            'risk_rank_range': {
                'min': tier_row.get('min_risk_rank', 0),
                'max': tier_row.get('max_risk_rank', 0)
            },
            'verdict_distribution': verdict_by_tier.get(tier_name, {}),
            'last_computed': tier_row.get('last_computed')
        }
        
        if include_servers:
            servers_sql = f"""
                SELECT 
                    r.server_id,
                    s.name,
                    s.verdict,
                    s.trust_score,
                    r.risk_rank
                FROM mcp_risk_register r
                JOIN mcp_server_registry s ON r.server_id = s.server_id
                WHERE r.risk_tier = '{tier_name}'{tier_filter}
                ORDER BY r.risk_rank DESC
                LIMIT 100
            """
            tier_entry['servers'] = ws_query(servers_sql)
        
        result['tiers'].append(tier_entry)
    
    return result


@app.get('/api/v1/risk-tier/{tier}/servers')
def get_tier_servers(
    tier: str,
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    sort_by: str = Query('risk_rank', enum=['risk_rank', 'trust_score', 'name']),
    order: str = Query('desc', enum=['asc', 'desc'])
):
    """
    Get servers in a specific risk tier.
    """
    order_dir = 'DESC' if order == 'desc' else 'ASC'
    
    servers_sql = f"""
        SELECT 
            r.server_id,
            s.name,
            s.description,
            s.url,
            s.verdict,
            s.trust_score,
            r.risk_rank,
            r.threat_count,
            r.computed_at
        FROM mcp_risk_register r
        JOIN mcp_server_registry s ON r.server_id = s.server_id
        WHERE r.risk_tier = '{tier}'
        ORDER BY 
            CASE WHEN '{sort_by}' = 'risk_rank' THEN r.risk_rank END {order_dir},
            CASE WHEN '{sort_by}' = 'trust_score' THEN s.trust_score END {order_dir},
            CASE WHEN '{sort_by}' = 'name' THEN s.name END ASC
        LIMIT {limit}
        OFFSET {offset}
    """
    servers = ws_query(servers_sql)
    
    count_sql = f"SELECT COUNT(*) as total FROM mcp_risk_register WHERE risk_tier = '{tier}'"
    count_result = ws_query(count_sql)
    total = count_result[0].get('total', 0) if count_result else 0
    
    return {
        'tier': tier,
        'servers': servers,
        'pagination': {
            'limit': limit,
            'offset': offset,
            'total': total,
            'has_more': offset + limit < total
        }
    }


@app.get('/api/v1/risk-tier/distribution')
def get_risk_distribution(
    group_by: str = Query('risk_tier', enum=['risk_tier', 'verdict', 'registry_source'])
):
    """
    Get risk distribution across servers, grouped by specified dimension.
    """
    if group_by == 'risk_tier':
        sql = """
            SELECT 
                risk_tier as group_key,
                COUNT(*) as count,
                ROUND(COUNT(*) * 100.0 / SUM(COUNT(*)) OVER(), 2) as percentage
            FROM mcp_risk_register
            GROUP BY risk_tier
            ORDER BY count DESC
        """
    elif group_by == 'verdict':
        sql = """
            SELECT 
                COALESCE(s.verdict, 'UNKNOWN') as group_key,
                COUNT(*) as count,
                ROUND(COUNT(*) * 100.0 / SUM(COUNT(*)) OVER(), 2) as percentage
            FROM mcp_risk_register r
            LEFT JOIN mcp_server_registry s ON r.server_id = s.server_id
            GROUP BY COALESCE(s.verdict, 'UNKNOWN')
            ORDER BY count DESC
        """
    else:
        sql = """
            SELECT 
                COALESCE(s.registry_source, 'UNKNOWN') as group_key,
                COUNT(*) as count,
                ROUND(COUNT(*) * 100.0 / SUM(COUNT(*)) OVER(), 2) as percentage
            FROM mcp_risk_register r
            LEFT JOIN mcp_server_registry s ON r.server_id = s.server_id
            GROUP BY COALESCE(s.registry_source, 'UNKNOWN')
            ORDER BY count DESC
        """
    
    data = ws_query(sql)
    
    return {
        'group_by': group_by,
        'distribution': data,
        'ts': datetime.now(timezone.utc).isoformat()
    }


@app.get('/api/v1/risk-tier/compare')
def compare_tiers(
    tiers: str = Query(..., description='Comma-separated tier names to compare'),
    metrics: Optional[str] = Query('count,avg_trust,threats', description='Metrics to compare')
):
    """
    Compare specific risk tiers side by side.
    """
    tier_list = [t.strip() for t in tiers.split(',')]
    tier_placeholders = "','".join(tier_list)
    
    comparison_sql = f"""
        SELECT 
            r.risk_tier,
            COUNT(DISTINCT r.server_id) as server_count,
            AVG(s.trust_score) as avg_trust_score,
            MIN(s.trust_score) as min_trust_score,
            MAX(s.trust_score) as max_trust_score,
            SUM(r.threat_count) as total_threats,
            AVG(r.threat_count) as avg_threats_per_server,
            AVG(r.risk_rank) as avg_risk_rank
        FROM mcp_risk_register r
        JOIN mcp_server_registry s ON r.server_id = s.server_id
        WHERE r.risk_tier IN ('{tier_placeholders}')
        GROUP BY r.risk_tier
    """
    tier_stats = ws_query(comparison_sql)
    
    metrics_list = [m.strip() for m in metrics.split(',')]
    comparison = {}
    
    for stat in tier_stats:
        tier_name = stat.get('risk_tier')
        comparison[tier_name] = {}
        
        if 'count' in metrics_list:
            comparison[tier_name]['server_count'] = stat.get('server_count', 0)
        if 'avg_trust' in metrics_list:
            comparison[tier_name]['avg_trust_score'] = round(stat.get('avg_trust_score', 0), 2)
        if 'threats' in metrics_list:
            comparison[tier_name]['total_threats'] = stat.get('total_threats', 0)
            comparison[tier_name]['avg_threats_per_server'] = round(stat.get('avg_threats_per_server', 0), 2)
        if 'risk_rank' in metrics_list:
            comparison[tier_name]['avg_risk_rank'] = round(stat.get('avg_risk_rank', 0), 2)
    
    return {
        'tiers': tier_list,
        'metrics': metrics_list,
        'comparison': comparison,
        'ts': datetime.now(timezone.utc).isoformat()
    }


@app.get('/api/v1/risk-tier/timeline')
def get_risk_timeline(
    server_id: Optional[str] = Query(None),
    tier: Optional[str] = Query(None),
    days: int = Query(30, ge=1, le=365)
):
    """
    Get risk tier changes over time.
    """
    if server_id:
        sql = f"""
            SELECT 
                server_id,
                risk_tier,
                risk_rank,
                computed_at
            FROM mcp_risk_register
            WHERE server_id = '{server_id}'
            ORDER BY computed_at DESC
            LIMIT {days}
        """
    elif tier:
        sql = f"""
            SELECT 
                DATE(computed_at) as date,
                COUNT(*) as servers_in_tier
            FROM mcp_risk_register
            WHERE risk_tier = '{tier}'
            AND computed_at >= NOW() - INTERVAL '{days} days'
            GROUP BY DATE(computed_at)
            ORDER BY date DESC
        """
    else:
        sql = f"""
            SELECT 
                DATE(computed_at) as date,
                risk_tier,
                COUNT(*) as server_count
            FROM mcp_risk_register
            WHERE computed_at >= NOW() - INTERVAL '{days} days'
            GROUP BY DATE(computed_at), risk_tier
            ORDER BY date DESC, risk_tier
        """
    
    data = ws_query(sql)
    
    return {
        'server_id': server_id,
        'tier': tier,
        'days': days,
        'timeline': data,
        'ts': datetime.now(timezone.utc).isoformat()
    }


@app.get('/api/v1/risk-tier/summary')
def get_risk_summary():
    """
    Get high-level risk tier summary statistics.
    """
    summary_sql = """
        SELECT 
            COUNT(DISTINCT server_id) as total_servers,
            COUNT(DISTINCT risk_tier) as tier_count,
            AVG(risk_rank) as global_avg_risk_rank,
            SUM(threat_count) as total_threats,
            MAX(computed_at) as last_computed
        FROM mcp_risk_register
    """
    summary = ws_query(summary_sql)
    
    tier_counts_sql = """
        SELECT risk_tier, COUNT(*) as count 
        FROM mcp_risk_register 
        GROUP BY risk_tier 
        ORDER BY count DESC
    """
    tier_counts = ws_query(tier_counts_sql)
    
    high_risk_sql = """
        SELECT COUNT(*) as high_risk_count
        FROM mcp_risk_register
        WHERE risk_tier IN ('HIGH_RISK', 'CRITICAL', 'KNOWN_THREAT')
    """
    high_risk = ws_query(high_risk_sql)
    
    return {
        'total_servers': summary[0].get('total_servers', 0) if summary else 0,
        'tier_count': summary[0].get('tier_count', 0) if summary else 0,
        'global_avg_risk_rank': round(summary[0].get('global_avg_risk_rank', 0), 2) if summary else 0,
        'total_threats': summary[0].get('total_threats', 0) if summary else 0,
        'high_risk_count': high_risk[0].get('high_risk_count', 0) if high_risk else 0,
        'tier_distribution': tier_counts,
        'last_computed': summary[0].get('last_computed') if summary else None,
        'ts': datetime.now(timezone.utc).isoformat()
    }


def run():
    """Run the FastAPI server."""
    import signal
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)
    
    if not check_single_instance():
        logger.error('Failed to acquire PID file, exiting')
        sys.exit(1)
    
    logger.info(f'Starting {SERVICE_NAME} on port {PORT}')
    send_heartbeat()
    
    uvicorn.run(app, host='0.0.0.0', port=PORT, log_level='info')


if __name__ == '__main__':
    run()