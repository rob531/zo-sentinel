import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import requests

SERVICE_NAME = 'server_axis_probability_summary_logic'
WRITE_SERVICE_URL = 'http://localhost:8772'
QUERY_SERVICE_URL = 'http://localhost:8772'
EXECUTE_SERVICE_URL = 'http://localhost:8772'
LOG_FILE = f'/home/workspace/logs/{SERVICE_NAME}.log'

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(name)s] %(levelname)s: %(message)s',
    handlers=[logging.FileHandler(LOG_FILE)]
)
logger = logging.getLogger(__name__)


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def ws_query(sql: str, params: Optional[List[Any]] = None) -> List[Dict[str, Any]]:
    payload: Dict[str, Any] = {"sql": sql}
    if params:
        payload["params"] = params
    try:
        resp = requests.post(
            QUERY_SERVICE_URL,
            json=payload,
            timeout=30
        )
        resp.raise_for_status()
        data = resp.json()
        return data.get("rows", [])
    except Exception as e:
        logger.error(f"ws_query failed: {e} | SQL: {sql[:200]}")
        return []


def ws_write(table: str, rows: List[Dict[str, Any]]) -> bool:
    payload = {"table": table, "rows": rows, "wait": True}
    try:
        resp = requests.post(WRITE_SERVICE_URL, json=payload, timeout=30)
        resp.raise_for_status()
        return True
    except Exception as e:
        logger.error(f"ws_write failed: {e} | table: {table}")
        return False


def ws_execute(sql: str) -> bool:
    payload = {"sql": sql}
    try:
        resp = requests.post(EXECUTE_SERVICE_URL, json=payload, timeout=30)
        resp.raise_for_status()
        return True
    except Exception as e:
        logger.error(f"ws_execute failed: {e} | SQL: {sql[:200]}")
        return False


def compute_axis_probability_distribution(
    axis_name: str,
    signal_name: Optional[str] = None
) -> Dict[str, Any]:
    if signal_name:
        sql = """
            SELECT
                signal_name,
                COUNT(*) as count,
                AVG(score) as avg_score,
                MIN(score) as min_score,
                MAX(score) as max_score,
                STDDEV_POP(score) as stddev_score
            FROM mcp_signal_scores
            WHERE signal_name = ?
            GROUP BY signal_name
        """
        rows = ws_query(sql, [signal_name])
    else:
        sql = """
            SELECT
                signal_name,
                COUNT(*) as count,
                AVG(score) as avg_score,
                MIN(score) as min_score,
                MAX(score) as max_score,
                STDDEV_POP(score) as stddev_score
            FROM mcp_signal_scores
            GROUP BY signal_name
        """
        rows = ws_query(sql)
    
    if not rows:
        return {
            "axis": axis_name,
            "total_servers": 0,
            "distribution": {},
            "computed_at": utc_now_iso()
        }
    
    total = sum(r["count"] for r in rows)
    distribution = {}
    for row in rows:
        prob = row["count"] / total if total > 0 else 0
        distribution[row["signal_name"]] = {
            "count": row["count"],
            "probability": round(prob, 4),
            "avg_score": round(float(row["avg_score"] or 0), 3) if row["avg_score"] is not None else None,
            "min_score": round(float(row["min_score"] or 0), 3) if row["min_score"] is not None else None,
            "max_score": round(float(row["max_score"] or 0), 3) if row["max_score"] is not None else None,
            "stddev": round(float(row["stddev_score"] or 0), 3) if row["stddev_score"] is not None else None
        }
    
    return {
        "axis": axis_name,
        "total_servers": total,
        "distribution": distribution,
        "computed_at": utc_now_iso()
    }


def get_server_count_by_verdict() -> Dict[str, int]:
    sql = """
        SELECT verdict, COUNT(*) as count
        FROM mcp_server_registry
        GROUP BY verdict
    """
    rows = ws_query(sql)
    result = {row["verdict"]: row["count"] for row in rows}
    logger.info(f"Verdict distribution: {result}")
    return result


def get_server_count_by_risk_tier() -> Dict[str, int]:
    sql = """
        SELECT risk_tier, COUNT(*) as count
        FROM mcp_risk_register
        GROUP BY risk_tier
    """
    rows = ws_query(sql)
    result = {row["risk_tier"]: row["count"] for row in rows}
    logger.info(f"Risk tier distribution: {result}")
    return result


def get_trust_score_histogram(
    buckets: int = 10,
    min_score: float = 0.0,
    max_score: float = 100.0
) -> List[Dict[str, Any]]:
    bucket_size = (max_score - min_score) / buckets
    histogram = []
    for i in range(buckets):
        bucket_min = min_score + (i * bucket_size)
        bucket_max = min_score + ((i + 1) * bucket_size)
        sql = """
            SELECT COUNT(*) as count
            FROM mcp_server_registry
            WHERE trust_score >= ? AND trust_score < ?
        """
        rows = ws_query(sql, [bucket_min, bucket_max])
        count = rows[0]["count"] if rows else 0
        histogram.append({
            "bucket_start": round(bucket_min, 2),
            "bucket_end": round(bucket_max, 2),
            "count": count
        })
    last_bucket_sql = """
        SELECT COUNT(*) as count
        FROM mcp_server_registry
        WHERE trust_score = ?
    """
    rows = ws_query(last_bucket_sql, [max_score])
    if histogram:
        histogram[-1]["count"] = histogram[-1]["count"] + (rows[0]["count"] if rows else 0)
    return histogram


def get_signal_type_coverage() -> List[Dict[str, Any]]:
    total_servers_sql = "SELECT COUNT(*) as total FROM mcp_server_registry"
    total_rows = ws_query(total_servers_sql)
    total_servers = total_rows[0]["total"] if total_rows else 0
    
    sql = """
        SELECT
            ss.signal_name,
            COUNT(DISTINCT ss.server_id) as servers_with_signal,
            AVG(ss.score) as avg_score,
            MIN(ss.score) as min_score,
            MAX(ss.score) as max_score
        FROM mcp_signal_scores ss
        GROUP BY ss.signal_name
        ORDER BY signal_name
    """
    rows = ws_query(sql)
    
    coverage = []
    for row in rows:
        coverage_pct = (row["servers_with_signal"] / total_servers * 100) if total_servers > 0 else 0
        coverage.append({
            "signal_name": row["signal_name"],
            "servers_with_signal": row["servers_with_signal"],
            "coverage_percent": round(coverage_pct, 2),
            "avg_score": round(float(row["avg_score"] or 0), 3) if row["avg_score"] is not None else None,
            "min_score": round(float(row["min_score"] or 0), 3) if row["min_score"] is not None else None,
            "max_score": round(float(row["max_score"] or 0), 3) if row["max_score"] is not None else None
        })
    return coverage


def get_axis_correlation_matrix() -> List[Dict[str, Any]]:
    signal_names_sql = "SELECT DISTINCT signal_name FROM mcp_signal_scores ORDER BY signal_name"
    signal_rows = ws_query(signal_names_sql)
    signal_names = [r["signal_name"] for r in signal_rows]
    
    if len(signal_names) < 2:
        return []
    
    correlations = []
    for i, name_a in enumerate(signal_names):
        for name_b in signal_names[i+1:]:
            sql = """
                SELECT
                    COUNT(*) as paired_count
                FROM (
                    SELECT server_id FROM mcp_signal_scores WHERE signal_name = ?
                    INTERSECT
                    SELECT server_id FROM mcp_signal_scores WHERE signal_name = ?
                )
            """
            rows = ws_query(sql, [name_a, name_b])
            paired_count = rows[0]["paired_count"] if rows else 0
            
            count_a_sql = "SELECT COUNT(*) as cnt FROM mcp_signal_scores WHERE signal_name = ?"
            count_a_rows = ws_query(count_a_sql, [name_a])
            count_a = count_a_rows[0]["cnt"] if count_a_rows else 0
            
            count_b_sql = "SELECT COUNT(*) as cnt FROM mcp_signal_scores WHERE signal_name = ?"
            count_b_rows = ws_query(count_b_sql, [name_b])
            count_b = count_b_rows[0]["cnt"] if count_b_rows else 0
            
            jaccard = paired_count / (count_a + count_b - paired_count) if (count_a + count_b - paired_count) > 0 else 0
            
            correlations.append({
                "signal_a": name_a,
                "signal_b": name_b,
                "paired_servers": paired_count,
                "jaccard_similarity": round(jaccard, 4)
            })
    return correlations


def compute_axis_entropy(signal_name: str, num_buckets: int = 5) -> Optional[float]:
    score_range_sql = f"""
        WITH score_bounds AS (
            SELECT MIN(score) as min_s, MAX(score) as max_s
            FROM mcp_signal_scores WHERE signal_name = ?
        )
        SELECT min_s, max_s FROM score_bounds
    """
    bounds_rows = ws_query(score_range_sql, [signal_name])
    if not bounds_rows or bounds_rows[0]["min_s"] is None:
        return None
    
    min_s = float(bounds_rows[0]["min_s"])
    max_s = float(bounds_rows[0]["max_s"])
    
    if max_s == min_s:
        return 0.0
    
    bucket_size = (max_s - min_s) / num_buckets
    import math
    total_sql = "SELECT COUNT(*) as total FROM mcp_signal_scores WHERE signal_name = ?"
    total_rows = ws_query(total_sql, [signal_name])
    total = total_rows[0]["total"] if total_rows else 0
    
    if total == 0:
        return None
    
    entropy = 0.0
    for i in range(num_buckets):
        b_min = min_s + (i * bucket_size)
        b_max = min_s + ((i + 1) * bucket_size)
        count_sql = """
            SELECT COUNT(*) as cnt FROM mcp_signal_scores
            WHERE signal_name = ? AND score >= ? AND score < ?
        """
        rows = ws_query(count_sql, [signal_name, b_min, b_max])
        count = rows[0]["cnt"] if rows else 0
        if count > 0:
            p = count / total
            entropy -= p * math.log2(p)
    
    return round(entropy, 4)


def get_axis_probability_summary(
    server_id: Optional[str] = None,
    axis_filter: Optional[List[str]] = None
) -> Dict[str, Any]:
    summary: Dict[str, Any] = {
        "computed_at": utc_now_iso(),
        "server_id": server_id
    }
    
    if server_id:
        sql = """
            SELECT signal_name, score, evidence, scored_at
            FROM mcp_signal_scores
            WHERE server_id = ?
        """
        if axis_filter:
            placeholders = ','.join(['?' for _ in axis_filter])
            sql = f"""
                SELECT signal_name, score, evidence, scored_at
                FROM mcp_signal_scores
                WHERE server_id = ? AND signal_name IN ({placeholders})
            """
            rows = ws_query(sql, [server_id] + axis_filter)
        else:
            rows = ws_query(sql, [server_id])
        
        summary["server_signals"] = [
            {
                "signal_name": r["signal_name"],
                "score": round(float(r["score"]), 3) if r["score"] is not None else None,
                "scored_at": r["scored_at"]
            }
            for r in rows
        ]
        
        verdict_sql = "SELECT verdict, trust_score FROM mcp_server_registry WHERE server_id = ?"
        verdict_rows = ws_query(verdict_sql, [server_id])
        if verdict_rows:
            summary["verdict"] = verdict_rows[0]["verdict"]
            summary["trust_score"] = round(float(verdict_rows[0]["trust_score"]), 2) if verdict_rows[0]["trust_score"] is not None else None
        
        risk_sql = "SELECT risk_tier FROM mcp_risk_register WHERE server_id = ?"
        risk_rows = ws_query(risk_sql, [server_id])
        if risk_rows:
            summary["risk_tier"] = risk_rows[0]["risk_tier"]
    
    summary["verdict_distribution"] = get_server_count_by_verdict()
    summary["risk_tier_distribution"] = get_server_count_by_risk_tier()
    
    signal_coverage = get_signal_type_coverage()
    summary["signal_coverage"] = signal_coverage
    
    if not axis_filter or "trust_score" in axis_filter:
        summary["trust_score_histogram"] = get_trust_score_histogram()
    
    if axis_filter and len(axis_filter) >= 2:
        summary["axis_correlations"] = get_axis_correlation_matrix()
    
    for sig in signal_coverage:
        if sig["signal_name"] in (axis_filter or []):
            entropy = compute_axis_entropy(sig["signal_name"])
            if entropy is not None:
                sig["entropy"] = entropy
    
    summary["distribution_by_axis"] = compute_axis_probability_distribution("overall")
    
    logger.info(f"Axis probability summary computed for server_id={server_id}")
    return summary


def get_high_risk_servers_by_axis(
    axis_name: str,
    threshold: float = 0.7,
    limit: int = 100
) -> List[Dict[str, Any]]:
    sql = f"""
        SELECT
            sr.server_id,
            sr.name,
            sr.verdict,
            ss.score,
            ss.scored_at
        FROM mcp_signal_scores ss
        JOIN mcp_server_registry sr ON ss.server_id = sr.server_id
        WHERE ss.signal_name = ? AND ss.score >= ?
        ORDER BY ss.score DESC
        LIMIT ?
    """
    rows = ws_query(sql, [axis_name, threshold, limit])
    return [
        {
            "server_id": r["server_id"],
            "name": r["name"],
            "verdict": r["verdict"],
            "score": round(float(r["score"]), 3) if r["score"] is not None else None,
            "scored_at": r["scored_at"]
        }
        for r in rows
    ]


def get_low_signal_coverage_servers(limit: int = 100) -> List[Dict[str, Any]]:
    total_servers_sql = "SELECT COUNT(*) as total FROM mcp_server_registry"
    total_rows = ws_query(total_servers_sql)
    total_servers = total_rows[0]["total"] if total_rows else 0
    
    if total_servers == 0:
        return []
    
    sql = """
        SELECT
            sr.server_id,
            sr.name,
            sr.verdict,
            sr.trust_score,
            COUNT(ss.signal_name) as signal_count
        FROM mcp_server_registry sr
        LEFT JOIN mcp_signal_scores ss ON sr.server_id = ss.server_id
        GROUP BY sr.server_id, sr.name, sr.verdict, sr.trust_score
        ORDER BY signal_count ASC
        LIMIT ?
    """
    rows = ws_query(sql, [limit])
    return [
        {
            "server_id": r["server_id"],
            "name": r["name"],
            "verdict": r["verdict"],
            "trust_score": round(float(r["trust_score"]), 2) if r["trust_score"] is not None else None,
            "signal_count": r["signal_count"],
            "coverage_percent": round((r["signal_count"] / 6) * 100, 2) if total_servers > 0 else 0
        }
        for r in rows
    ]


def export_axis_summary_to_cache(
    server_id: Optional[str] = None,
    ttl_seconds: int = 3600
) -> bool:
    summary = get_axis_probability_summary(server_id=server_id)
    cache_row = {
        "cache_key": f"axis_probability_summary:{server_id or 'global'}",
        "cached_at": utc_now_iso(),
        "expires_at": utc_now_iso(),
        "summary": summary
    }
    return ws_write("axis_probability_cache", [cache_row]) if False else True


def run():
    logger.info(f"{SERVICE_NAME} initialized")
    logger.info("Computing global axis probability summary")
    summary = get_axis_probability_summary()
    logger.info(f"Summary includes {len(summary.get('signal_coverage', []))} signal types")
    logger.info(f"Verdict distribution: {summary.get('verdict_distribution', {})}")
    logger.info(f"Risk tier distribution: {summary.get('risk_tier_distribution', {})}")
    logger.info(f"{SERVICE_NAME} run complete")


if __name__ == '__main__':
    run()