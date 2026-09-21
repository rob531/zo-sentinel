import logging
import os
from datetime import datetime, timezone
from typing import Any

import requests

LOG_DIR = os.path.dirname(os.path.abspath(__file__))
LOG_DIR = os.path.join(os.path.dirname(LOG_DIR), 'logs')
os.makedirs(LOG_DIR, exist_ok=True)
LOG_FILE = os.path.join(LOG_DIR, 'build_signal_scores_distribution_logic.log')

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(name)s] %(levelname)s: %(message)s',
    handlers=[logging.FileHandler(LOG_FILE), logging.StreamHandler()]
)
log = logging.getLogger(__name__)

SERVICE_NAME = 'build_signal_scores_distribution_logic'
WRITE_SERVICE_URL = 'http://localhost:8772'
QUERY_SERVICE_URL = 'http://localhost:8772/query'
EXECUTE_SERVICE_URL = 'http://localhost:8772/execute'
PORT = None
PID_FILE = None

REQUEST_TIMEOUT = 30


def ws_query(sql: str) -> list[dict[str, Any]]:
    """Query DuckDB via write_service HTTP endpoint."""
    payload = {'sql': sql}
    resp = requests.post(QUERY_SERVICE_URL, json=payload, timeout=REQUEST_TIMEOUT)
    resp.raise_for_status()
    data = resp.json()
    return data.get('rows', [])


def ws_write(table: str, rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Write to DuckDB via write_service HTTP endpoint."""
    payload = {'table': table, 'rows': rows, 'wait': True}
    resp = requests.post(WRITE_SERVICE_URL, json=payload, timeout=REQUEST_TIMEOUT)
    resp.raise_for_status()
    return resp.json()


def ws_execute(sql: str) -> dict[str, Any]:
    """Execute DDL/DML via write_service HTTP endpoint."""
    payload = {'sql': sql}
    resp = requests.post(EXECUTE_SERVICE_URL, json=payload, timeout=REQUEST_TIMEOUT)
    resp.raise_for_status()
    return resp.json()


def utc_now_iso() -> str:
    """Return current UTC timestamp in ISO 8601 format."""
    return datetime.now(timezone.utc).isoformat()


def get_signal_scores_distribution() -> list[dict[str, Any]]:
    """Compute score distribution across all signal types and score ranges."""
    sql = """
    SELECT
        signal_name,
        score_bucket,
        COUNT(*) as server_count,
        MIN(score) as min_score,
        MAX(score) as max_score,
        AVG(score) as avg_score,
        STDDEV(score) as stddev_score
    FROM (
        SELECT
            signal_name,
            score,
            CASE
                WHEN score < 0.2 THEN '0.0-0.2'
                WHEN score < 0.4 THEN '0.2-0.4'
                WHEN score < 0.6 THEN '0.4-0.6'
                WHEN score < 0.8 THEN '0.6-0.8'
                ELSE '0.8-1.0'
            END as score_bucket
        FROM mcp_signal_scores
        WHERE score IS NOT NULL
    ) sub
    GROUP BY signal_name, score_bucket, score
    ORDER BY signal_name, score_bucket
    """
    return ws_query(sql)


def get_overall_score_distribution() -> list[dict[str, Any]]:
    """Compute overall score distribution across all signals."""
    sql = """
    SELECT
        CASE
            WHEN score < 0.2 THEN '0.0-0.2'
            WHEN score < 0.4 THEN '0.2-0.4'
            WHEN score < 0.6 THEN '0.4-0.6'
            WHEN score < 0.8 THEN '0.6-0.8'
            ELSE '0.8-1.0'
        END as score_bucket,
        COUNT(DISTINCT server_id) as server_count,
        COUNT(*) as total_signals,
        MIN(score) as min_score,
        MAX(score) as max_score,
        AVG(score) as avg_score,
        STDDEV(score) as stddev_score
    FROM mcp_signal_scores
    WHERE score IS NOT NULL
    GROUP BY score_bucket
    ORDER BY score_bucket
    """
    return ws_query(sql)


def get_signal_type_counts() -> list[dict[str, Any]]:
    """Get count of servers scored per signal type."""
    sql = """
    SELECT
        signal_name,
        COUNT(DISTINCT server_id) as servers_scored,
        COUNT(*) as total_records,
        COUNT(DISTINCT score) as distinct_scores,
        MIN(scored_at) as first_score,
        MAX(scored_at) as latest_score
    FROM mcp_signal_scores
    GROUP BY signal_name
    ORDER BY signal_name
    """
    return ws_query(sql)


def get_score_percentiles() -> list[dict[str, Any]]:
    """Compute percentiles for each signal type."""
    sql = """
    SELECT
        signal_name,
        COUNT(*) as total,
        AVG(score) as mean,
        APPROX_QUANTILE(score, 0.25) as p25,
        APPROX_QUANTILE(score, 0.5) as p50,
        APPROX_QUANTILE(score, 0.75) as p75,
        APPROX_QUANTILE(score, 0.9) as p90,
        APPROX_QUANTILE(score, 0.95) as p95,
        APPROX_QUANTILE(score, 0.99) as p99
    FROM mcp_signal_scores
    WHERE score IS NOT NULL
    GROUP BY signal_name
    ORDER BY signal_name
    """
    return ws_query(sql)


def get_verdict_score_correlation() -> list[dict[str, Any]]:
    """Analyze correlation between trust_score and individual signal scores."""
    sql = """
    SELECT
        r.verdict,
        ss.signal_name,
        COUNT(DISTINCT ss.server_id) as servers,
        AVG(ss.score) as avg_signal_score,
        AVG(r.trust_score) as avg_trust_score,
        CORR(ss.score, r.trust_score) as correlation
    FROM mcp_signal_scores ss
    JOIN mcp_server_registry r ON ss.server_id = r.server_id
    WHERE ss.score IS NOT NULL AND r.trust_score IS NOT NULL
    GROUP BY r.verdict, ss.signal_name
    ORDER BY r.verdict, ss.signal_name
    """
    return ws_query(sql)


def compute_distribution_id(signal_name: str, bucket: str, ts: str) -> str:
    """Compute deterministic ID for distribution records."""
    import hashlib
    content = f"{signal_name}:{bucket}:{ts}"
    return hashlib.sha256(content.encode()).hexdigest()[:16]


def persist_distribution_snapshot(
    signal_distribution: list[dict[str, Any]],
    overall_distribution: list[dict[str, Any]],
    signal_type_counts: list[dict[str, Any]],
    score_percentiles: list[dict[str, Any]],
    verdict_correlation: list[dict[str, Any]]
) -> None:
    """Persist distribution snapshot to signal_scores_distribution table."""
    ts = utc_now_iso()
    
    ws_execute("""
        CREATE TABLE IF NOT EXISTS signal_scores_distribution (
            dist_id VARCHAR,
            snapshot_ts TIMESTAMPTZ,
            signal_name VARCHAR,
            score_bucket VARCHAR,
            server_count BIGINT,
            min_score DOUBLE,
            max_score DOUBLE,
            avg_score DOUBLE,
            stddev_score DOUBLE,
            record_type VARCHAR,
            created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
        )
    """)

    rows = []
    for row in signal_distribution:
        rows.append({
            'dist_id': compute_distribution_id(
                row.get('signal_name', ''),
                row.get('score_bucket', ''),
                ts
            ),
            'snapshot_ts': ts,
            'signal_name': row.get('signal_name', ''),
            'score_bucket': row.get('score_bucket', ''),
            'server_count': row.get('server_count', 0),
            'min_score': row.get('min_score'),
            'max_score': row.get('max_score'),
            'avg_score': row.get('avg_score'),
            'stddev_score': row.get('stddev_score'),
            'record_type': 'signal_bucket'
        })

    for row in overall_distribution:
        rows.append({
            'dist_id': compute_distribution_id('OVERALL', row.get('score_bucket', ''), ts),
            'snapshot_ts': ts,
            'signal_name': 'OVERALL',
            'score_bucket': row.get('score_bucket', ''),
            'server_count': row.get('server_count', 0),
            'min_score': row.get('min_score'),
            'max_score': row.get('max_score'),
            'avg_score': row.get('avg_score'),
            'stddev_score': row.get('stddev_score'),
            'record_type': 'overall_bucket'
        })

    if rows:
        ws_write('signal_scores_distribution', rows)

    log.info(f"Persisted {len(rows)} distribution records at {ts}")


def run() -> dict[str, Any]:
    """Run signal scores distribution analysis."""
    log.info("Starting signal scores distribution analysis")
    
    try:
        signal_distribution = get_signal_scores_distribution()
        log.info(f"Retrieved {len(signal_distribution)} signal distribution rows")
    except Exception as e:
        log.error(f"Failed to get signal distribution: {e}")
        signal_distribution = []

    try:
        overall_distribution = get_overall_score_distribution()
        log.info(f"Retrieved {len(overall_distribution)} overall distribution rows")
    except Exception as e:
        log.error(f"Failed to get overall distribution: {e}")
        overall_distribution = []

    try:
        signal_type_counts = get_signal_type_counts()
        log.info(f"Retrieved {len(signal_type_counts)} signal type counts")
    except Exception as e:
        log.error(f"Failed to get signal type counts: {e}")
        signal_type_counts = []

    try:
        score_percentiles = get_score_percentiles()
        log.info(f"Retrieved {len(score_percentiles)} percentile records")
    except Exception as e:
        log.error(f"Failed to get score percentiles: {e}")
        score_percentiles = []

    try:
        verdict_correlation = get_verdict_score_correlation()
        log.info(f"Retrieved {len(verdict_correlation)} verdict correlation records")
    except Exception as e:
        log.error(f"Failed to get verdict correlation: {e}")
        verdict_correlation = []

    try:
        persist_distribution_snapshot(
            signal_distribution,
            overall_distribution,
            signal_type_counts,
            score_percentiles,
            verdict_correlation
        )
    except Exception as e:
        log.error(f"Failed to persist distribution snapshot: {e}")

    result = {
        'signal_distribution': signal_distribution,
        'overall_distribution': overall_distribution,
        'signal_type_counts': signal_type_counts,
        'score_percentiles': score_percentiles,
        'verdict_correlation': verdict_correlation,
        'snapshot_ts': utc_now_iso()
    }
    
    log.info("Signal scores distribution analysis complete")
    return result


if __name__ == '__main__':
    result = run()
    log.info(f"Distribution analysis complete: {len(result.get('signal_distribution', []))} signal distributions processed")
    sys.exit(0)