import logging
import sys
from datetime import datetime, timezone
from collections import Counter

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(name)s] %(levelname)s: %(message)s',
    filename='/home/workspace/logs/diagnose_enrichment_cardinality.log'
)
LOG = logging.getLogger(__name__)

SERVICE_NAME = "diagnose_enrichment_cardinality"
WRITE_SERVICE_URL = 'http://localhost:8772'

SIGNAL_TYPES = ['permission_scope', 'temporal_stability', 'tool_description_safety']


def ws_query(sql: str, params: tuple = None) -> list:
    import requests
    payload = {"sql": sql, "params": params if params else []}
    resp = requests.post(
        f"{WRITE_SERVICE_URL}/query",
        json=payload,
        timeout=30
    )
    resp.raise_for_status()
    result = resp.json()
    if result.get("status") == "error":
        raise Exception(f"Query error: {result.get('message')}")
    return result.get("rows", [])


def ws_write(table: str, rows: list) -> dict:
    import requests
    payload = {"table": table, "rows": rows, "wait": True}
    resp = requests.post(
        f"{WRITE_SERVICE_URL}/write",
        json=payload,
        timeout=30
    )
    resp.raise_for_status()
    return resp.json()


def collect_enrichment_scores() -> dict:
    placeholders = ','.join(['?' for _ in SIGNAL_TYPES])
    sql = f"""
    SELECT 
        signal_type,
        signal_score,
        COUNT(*) as row_count,
        MIN(computed_at) as first_seen,
        MAX(computed_at) as last_seen
    FROM mcp_signal_enrichments
    WHERE signal_type IN ({placeholders})
    GROUP BY signal_type, signal_score
    ORDER BY signal_type, signal_score
    """
    return ws_query(sql, SIGNAL_TYPES)


def analyze_cardinality(rows: list) -> dict:
    by_signal = {}
    for row in rows:
        sig_type = row.get('signal_type')
        if sig_type not in by_signal:
            by_signal[sig_type] = {
                'distinct_scores': set(),
                'total_rows': 0,
                'score_distribution': Counter(),
                'first_seen': None,
                'last_seen': None
            }
        score = row.get('signal_score')
        count = row.get('row_count', 0)
        by_signal[sig_type]['distinct_scores'].add(score)
        by_signal[sig_type]['score_distribution'][score] = count
        by_signal[sig_type]['total_rows'] += count
        
        row_first = row.get('first_seen')
        row_last = row.get('last_seen')
        if row_first:
            if not by_signal[sig_type]['first_seen'] or row_first < by_signal[sig_type]['first_seen']:
                by_signal[sig_type]['first_seen'] = row_first
        if row_last:
            if not by_signal[sig_type]['last_seen'] or row_last > by_signal[sig_type]['last_seen']:
                by_signal[sig_type]['last_seen'] = row_last
    
    for sig_type in by_signal:
        by_signal[sig_type]['distinct_count'] = len(by_signal[sig_type]['distinct_scores'])
        by_signal[sig_type]['distinct_scores'] = sorted(by_signal[sig_type]['distinct_scores'])
    
    return by_signal


def check_raw_source_data():
    placeholders = ','.join(['?' for _ in SIGNAL_TYPES])
    sql = f"""
    SELECT 
        signal_type,
        COUNT(DISTINCT signal_score) as distinct_scores,
        MIN(signal_score) as min_score,
        MAX(signal_score) as max_score,
        COUNT(*) as total_rows
    FROM mcp_signal_enrichments
    WHERE signal_type IN ({placeholders})
    GROUP BY signal_type
    """
    return ws_query(sql, SIGNAL_TYPES)


def check_computed_scores_table():
    sql = """
    SELECT 
        signal_type,
        COUNT(DISTINCT computed_score) as distinct_computed,
        MIN(computed_score) as min_computed,
        MAX(computed_score) as max_computed
    FROM mcp_signal_scores
    WHERE signal_type IN ('permission_scope', 'temporal_stability', 'tool_description_safety')
    GROUP BY signal_type
    """
    try:
        return ws_query(sql)
    except Exception as e:
        LOG.warning(f"Could not query mcp_signal_scores: {e}")
        return []


def diagnose():
    LOG.info("Collecting enrichment score distribution...")
    enrichment_rows = collect_enrichment_scores()
    
    LOG.info("Analyzing cardinality...")
    analysis = analyze_cardinality(enrichment_rows)
    
    LOG.info("Checking raw source data stats...")
    source_stats = check_raw_source_data()
    
    LOG.info("Checking computed_scores table...")
    computed_stats = check_computed_scores_table()
    
    findings = []
    
    for sig_type in SIGNAL_TYPES:
        finding = {
            'signal_type': sig_type,
            'status': 'OK',
            'distinct_scores': 0,
            'score_values': [],
            'distribution': {},
            'diagnosis': '',
            'ts': datetime.now(timezone.utc).isoformat()
        }
        
        if sig_type in analysis:
            a = analysis[sig_type]
            finding['distinct_scores'] = a['distinct_count']
            finding['score_values'] = a['distinct_scores']
            finding['distribution'] = dict(a['score_distribution'])
            finding['total_rows'] = a['total_rows']
            finding['first_seen'] = a['first_seen']
            finding['last_seen'] = a['last_seen']
            
            if a['distinct_count'] <= 4:
                finding['status'] = 'LOW_CARDINALITY'
                finding['diagnosis'] = (
                    f"Only {a['distinct_count']} distinct scores found: {a['distinct_scores']}. "
                    f"This suggests the enrichment formula may be capping or discretizing output. "
                    f"Check the enrichment_v2 implementation for hard-coded thresholds or bucket assignments."
                )
                findings.append(finding)
            else:
                finding['diagnosis'] = f"Cardinality is {a['distinct_count']}, which is healthy."
                findings.append(finding)
        else:
            finding['status'] = 'NO_DATA'
            finding['diagnosis'] = f"No enrichment data found for signal type '{sig_type}'."
            findings.append(finding)
    
    for stat in source_stats:
        sig_type = stat.get('signal_type')
        for f in findings:
            if f['signal_type'] == sig_type:
                f['source_distinct'] = stat.get('distinct_scores')
                f['source_min'] = stat.get('min_score')
                f['source_max'] = stat.get('max_score')
                f['source_total'] = stat.get('total_rows')
    
    LOG.info("Writing diagnostic results...")
    ws_write('diagnostic_enrichment_cardinality', findings)
    
    LOG.info("=== DIAGNOSIS SUMMARY ===")
    for f in findings:
        LOG.info(f"Signal: {f['signal_type']}")
        LOG.info(f"  Status: {f['status']}")
        LOG.info(f"  Distinct Scores: {f['distinct_scores']}")
        LOG.info(f"  Score Values: {f.get('score_values', [])}")
        if f.get('distribution'):
            LOG.info(f"  Distribution: {f['distribution']}")
        LOG.info(f"  Diagnosis: {f['diagnosis']}")
        LOG.info("")
    
    low_cardinality_count = sum(1 for f in findings if f['status'] == 'LOW_CARDINALITY')
    if low_cardinality_count > 0:
        LOG.warning(f"Found {low_cardinality_count} signal types with low cardinality (≤4 distinct scores)")
    
    return findings


if __name__ == '__main__':
    try:
        results = diagnose()
        sys.exit(0)
    except Exception as e:
        LOG.error(f"Diagnostic failed: {e}", exc_info=True)
        sys.exit(1)