import os
import sys
import logging
import hashlib
import requests
from datetime import datetime, timezone

# === House Convention Constants ===
SERVICE_NAME = 'signal_scores_cardinality_verification'
WRITE_SERVICE_URL = 'http://localhost:8772'

# === Logger Setup ===
LOG_FILE = f'/home/workspace/logs/{SERVICE_NAME}.log'
logger = logging.getLogger(__name__)
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(name)s] %(levelname)s: %(message)s',
    handlers=[logging.FileHandler(LOG_FILE)]
)

# === Helper Functions ===
def ws_query(sql, params=None):
    """Query via write_service HTTP endpoint."""
    payload = {'sql': sql}
    if params:
        payload['params'] = params
    try:
        resp = requests.post(
            f'{WRITE_SERVICE_URL}/query',
            json=payload,
            timeout=30
        )
        resp.raise_for_status()
        result = resp.json()
        return result.get('rows', [])
    except Exception as e:
        logger.error(f"ws_query failed: {e}")
        return []

def ws_write(table, rows):
    """Write rows via write_service HTTP endpoint."""
    if not isinstance(rows, list):
        rows = [rows]
    payload = {'table': table, 'rows': rows, 'wait': True}
    try:
        resp = requests.post(
            f'{WRITE_SERVICE_URL}/write',
            json=payload,
            timeout=30
        )
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        logger.error(f"ws_write failed: {e}")
        return None

def send_heartbeat(status='running', meta=None):
    """Send heartbeat to service_health."""
    ts = datetime.now(timezone.utc).isoformat()
    row = {
        'service_name': SERVICE_NAME,
        'status': status,
        'ts': ts,
        'meta': meta or {}
    }
    ws_write('service_health', row)

def check_signal_type_coverage():
    """Check which signal types exist per server and identify gaps."""
    
    # Expected 8 signal types from signal_analyser
    expected_signal_types = [
        'health_score',
        'capability_score', 
        'trust_score',
        'availability_score',
        'latency_score',
        'error_rate_score',
        'version_match_score',
        'attestation_score'
    ]
    
    logger.info("Starting signal cardinality verification...")
    
    # 1. Check schema of mcp_signal_scores
    schema_query = """
    SELECT column_name, data_type 
    FROM information_schema.columns 
    WHERE table_name = 'mcp_signal_scores'
    ORDER BY ordinal_position
    """
    schema = ws_query(schema_query)
    logger.info(f"mcp_signal_scores schema: {schema}")
    
    # 2. Get distinct signal_types currently in the table
    distinct_signals_query = """
    SELECT DISTINCT signal_type, COUNT(*) as cnt
    FROM mcp_signal_scores
    GROUP BY signal_type
    ORDER BY signal_type
    """
    existing_signals = ws_query(distinct_signals_query)
    logger.info(f"Signal types in table: {existing_signals}")
    
    # 3. Get total servers in mcp_server_registry
    servers_query = "SELECT COUNT(*) as total_servers FROM mcp_server_registry"
    server_count_result = ws_query(servers_query)
    total_servers = server_count_result[0]['total_servers'] if server_count_result else 0
    logger.info(f"Total servers in registry: {total_servers}")
    
    # 4. Get total rows in mcp_signal_scores
    total_rows_query = "SELECT COUNT(*) as total_scores FROM mcp_signal_scores"
    rows_result = ws_query(total_rows_query)
    total_scores = rows_result[0]['total_scores'] if rows_result else 0
    logger.info(f"Total rows in mcp_signal_scores: {total_scores}")
    
    # 5. Check per-server coverage - servers with at least one signal
    server_coverage_query = """
    SELECT COUNT(DISTINCT server_id) as servers_with_signals
    FROM mcp_signal_scores
    """
    coverage_result = ws_query(server_coverage_query)
    servers_with_signals = coverage_result[0]['servers_with_signals'] if coverage_result else 0
    logger.info(f"Servers with at least one signal: {servers_with_signals}")
    
    # 6. Cross-tab: which signal_types exist per server_id (sample)
    # This shows the "heatmap" of signal coverage
    cross_tab_query = """
    SELECT 
        server_id,
        signal_type,
        COUNT(*) as cnt
    FROM mcp_signal_scores
    GROUP BY server_id, signal_type
    ORDER BY server_id, signal_type
    LIMIT 100
    """
    cross_tab = ws_query(cross_tab_query)
    logger.info(f"Sample cross-tab (first 100 rows): {len(cross_tab)} rows")
    
    # 7. Find servers with MISSING signal types (gap analysis)
    # For each server, which expected signals are missing?
    gap_analysis_query = """
    WITH server_signals AS (
        SELECT DISTINCT server_id, signal_type
        FROM mcp_signal_scores
    ),
    all_servers AS (
        SELECT server_id FROM mcp_server_registry
    ),
    expected_pairs AS (
        SELECT s.server_id, st.signal_type
        FROM all_servers s
        CROSS JOIN (
            SELECT 'health_score' as signal_type
            UNION ALL SELECT 'capability_score'
            UNION ALL SELECT 'trust_score'
            UNION ALL SELECT 'availability_score'
            UNION ALL SELECT 'latency_score'
            UNION ALL SELECT 'error_rate_score'
            UNION ALL SELECT 'version_match_score'
            UNION ALL SELECT 'attestation_score'
        ) st
    )
    SELECT 
        ep.server_id,
        ep.signal_type,
        CASE WHEN ss.server_id IS NULL THEN 'MISSING' ELSE 'PRESENT' END as status
    FROM expected_pairs ep
    LEFT JOIN server_signals ss ON ep.server_id = ss.server_id AND ep.signal_type = ss.signal_type
    WHERE ss.server_id IS NULL
    ORDER BY ep.server_id, ep.signal_type
    LIMIT 50
    """
    gaps = ws_query(gap_analysis_query)
    logger.info(f"Missing signals sample (first 50 gaps): {len(gaps)} gap rows")
    
    # 8. Count gaps per signal type
    gap_counts_query = """
    WITH server_signals AS (
        SELECT DISTINCT server_id, signal_type
        FROM mcp_signal_scores
    ),
    all_servers AS (
        SELECT server_id FROM mcp_server_registry
    ),
    expected_pairs AS (
        SELECT s.server_id, st.signal_type
        FROM all_servers s
        CROSS JOIN (
            SELECT 'health_score' as signal_type
            UNION ALL SELECT 'capability_score'
            UNION ALL SELECT 'trust_score'
            UNION ALL SELECT 'availability_score'
            UNION ALL SELECT 'latency_score'
            UNION ALL SELECT 'error_rate_score'
            UNION ALL SELECT 'version_match_score'
            UNION ALL SELECT 'attestation_score'
        ) st
    )
    SELECT 
        ep.signal_type,
        COUNT(*) as missing_count
    FROM expected_pairs ep
    LEFT JOIN server_signals ss ON ep.server_id = ss.server_id AND ep.signal_type = ss.signal_type
    WHERE ss.server_id IS NULL
    GROUP BY ep.signal_type
    ORDER BY ep.signal_type
    """
    gap_counts = ws_query(gap_counts_query)
    logger.info(f"Missing counts per signal type: {gap_counts}")
    
    # 9. Summary report
    summary = {
        'total_servers': total_servers,
        'total_signal_rows': total_scores,
        'servers_with_signals': servers_with_signals,
        'expected_signals_per_server': 8,
        'signals_in_table': [s['signal_type'] for s in existing_signals] if existing_signals else [],
        'signal_counts': {s['signal_type']: s['cnt'] for s in existing_signals} if existing_signals else {},
        'gap_counts_per_type': {g['signal_type']: g['missing_count'] for g in gap_counts} if gap_counts else {},
        'missing_signal_types': [g for g in gap_counts if g['missing_count'] > 0] if gap_counts else []
    }
    
    # Log summary
    logger.info("=" * 60)
    logger.info("SIGNAL CARDINALITY VERIFICATION SUMMARY")
    logger.info("=" * 60)
    logger.info(f"Total servers: {summary['total_servers']}")
    logger.info(f"Total signal rows: {summary['total_signal_rows']}")
    logger.info(f"Servers with at least one signal: {summary['servers_with_signals']}")
    logger.info(f"Expected signals per server: {summary['expected_signals_per_server']}")
    logger.info(f"Signals in table: {summary['signals_in_table']}")
    logger.info(f"Signal counts: {summary['signal_counts']}")
    logger.info(f"Gap counts per type: {summary['gap_counts_per_type']}")
    
    # Calculate expected vs actual
    expected_total = total_servers * 8
    logger.info(f"Expected total rows (servers * 8): {expected_total}")
    logger.info(f"Actual total rows: {total_scores}")
    coverage_pct = (total_scores / expected_total * 100) if expected_total > 0 else 0
    logger.info(f"Coverage: {coverage_pct:.1f}%")
    
    # Write diagnostic results to a diagnostics table
    diagnostic_row = {
        'check_name': 'signal_scores_cardinality_verification',
        'check_ts': datetime.now(timezone.utc).isoformat(),
        'total_servers': total_servers,
        'total_signal_rows': total_scores,
        'expected_total': expected_total,
        'coverage_pct': round(coverage_pct, 2),
        'servers_covered': servers_with_signals,
        'signal_types_present': len(existing_signals) if existing_signals else 0,
        'missing_signals_summary': str(summary['gap_counts_per_type']),
        'findings': 'LOW COVERAGE' if coverage_pct < 50 else ('MODERATE COVERAGE' if coverage_pct < 90 else 'GOOD COVERAGE')
    }
    
    ws_write('diagnostic_check_results', diagnostic_row)
    logger.info(f"Diagnostic written: {diagnostic_row['findings']}")
    
    return summary

def main():
    """Run cardinality verification and exit."""
    logger.info("Starting signal scores cardinality verification...")
    
    try:
        result = check_signal_type_coverage()
        send_heartbeat(status='completed', meta={'result': 'completed'})
        logger.info("Verification complete. Exiting.")
        sys.exit(0)
    except Exception as e:
        logger.error(f"Verification failed: {e}")
        send_heartbeat(status='error', meta={'error': str(e)})
        sys.exit(1)

if __name__ == '__main__':
    main()