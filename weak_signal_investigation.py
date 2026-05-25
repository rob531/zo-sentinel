import os
import sys
import json
import logging
from datetime import datetime, timezone
from collections import Counter

import requests

SERVICE_NAME = "weak_signal_investigation"
WRITE_SERVICE_URL = "http://localhost:8772"

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(name)s] %(levelname)s: %(message)s',
    handlers=[
        logging.FileHandler(f'/home/workspace/logs/{SERVICE_NAME}.log'),
    ]
)
logger = logging.getLogger(__name__)


def ws_write(table, rows):
    """Write rows to write_service."""
    payload = {
        "table": table,
        "rows": rows,
        "wait": True
    }
    resp = requests.post(WRITE_SERVICE_URL + "/write", json=payload, timeout=30)
    resp.raise_for_status()
    return resp.json()


def ws_query(sql, params=None):
    """Query via write_service read endpoint."""
    payload = {"sql": sql, "params": params if params else []}
    resp = requests.post(WRITE_SERVICE_URL + "/query", json=payload, timeout=30)
    resp.raise_for_status()
    result = resp.json()
    if isinstance(result, dict) and "error" in result:
        raise RuntimeError(f"Query error: {result['error']}")
    return result


def get_table_columns(table_name):
    """Get column list for a table."""
    sql = """
        SELECT column_name 
        FROM information_schema.columns 
        WHERE table_name = ?
        ORDER BY ordinal_position
    """
    rows = ws_query(sql, params=(table_name,))
    return [r['column_name'] for r in rows]


def analyze_distinct_values(table, column, sample_limit=500):
    """Count distinct values in a column."""
    sql = f"""
        SELECT COUNT(DISTINCT {column}) as distinct_count,
               COUNT(*) as total_count
        FROM {table}
        WHERE {column} IS NOT NULL
    """
    count_rows = ws_query(sql)
    if not count_rows:
        return None
    
    total = count_rows[0]['total_count']
    distinct = count_rows[0]['distinct_count']
    
    if total == 0:
        return {"distinct": 0, "total": 0, "distribution": {}}
    
    value_sql = f"""
        SELECT {column} as val, COUNT(*) as cnt
        FROM {table}
        WHERE {column} IS NOT NULL
        GROUP BY {column}
        ORDER BY cnt DESC
        LIMIT ?
    """
    dist_rows = ws_query(value_sql, params=(sample_limit,))
    
    distribution = {}
    for row in dist_rows:
        val = row['val']
        cnt = row['cnt']
        distribution[str(val)] = cnt
    
    return {
        "distinct": distinct,
        "total": total,
        "distribution": distribution
    }


def analyze_signal_enrichments(target_fields):
    """Analyze mcp_signal_enrichments for target fields."""
    logger.info("=" * 60)
    logger.info("ANALYZING mcp_signal_enrichments")
    logger.info("=" * 60)
    
    results = {}
    
    for field in target_fields:
        logger.info(f"\nField: {field}")
        logger.info("-" * 40)
        
        data = analyze_distinct_values('mcp_signal_enrichments', field)
        
        if data:
            results[field] = data
            logger.info(f"  Distinct values: {data['distinct']}")
            logger.info(f"  Total rows: {data['total']}")
            logger.info("  Value distribution:")
            for val, cnt in sorted(data['distribution'].items()):
                logger.info(f"    {val}: {cnt}")
        else:
            logger.warning(f"  No data returned for {field}")
            results[field] = {"distinct": 0, "total": 0, "distribution": {}}
    
    return results


def analyze_signal_scores(target_signal_types):
    """Analyze mcp_signal_scores for target signal types."""
    logger.info("\n" + "=" * 60)
    logger.info("ANALYZING mcp_signal_scores")
    logger.info("=" * 60)
    
    results = {}
    
    for sig_type in target_signal_types:
        logger.info(f"\nSignal type: {sig_type}")
        logger.info("-" * 40)
        
        sql = f"""
            SELECT COUNT(DISTINCT signal_id) as signal_count,
                   COUNT(*) as score_rows,
                   MIN(raw_score) as min_raw,
                   MAX(raw_score) as max_raw,
                   MIN(normalized_score) as min_norm,
                   MAX(normalized_score) as max_norm
            FROM mcp_signal_scores
            WHERE signal_type = ?
        """
        rows = ws_query(sql, params=(sig_type,))
        
        if rows and rows[0]['signal_count'] > 0:
            r = rows[0]
            logger.info(f"  Unique signals: {r['signal_count']}")
            logger.info(f"  Score rows: {r['score_rows']}")
            logger.info(f"  Raw score range: {r['min_raw']} - {r['max_raw']}")
            logger.info(f"  Normalized score range: {r['min_norm']} - {r['max_norm']}")
            
            results[sig_type] = {
                "signal_count": r['signal_count'],
                "score_rows": r['score_rows'],
                "raw_range": [r['min_raw'], r['max_raw']],
                "norm_range": [r['min_norm'], r['max_norm']]
            }
            
            distinct_sql = """
                SELECT COUNT(DISTINCT normalized_score) as distinct_norm
                FROM mcp_signal_scores
                WHERE signal_type = ?
            """
            dist_rows = ws_query(distinct_sql, params=(sig_type,))
            if dist_rows:
                results[sig_type]['distinct_normalized'] = dist_rows[0]['distinct_norm']
                logger.info(f"  Distinct normalized scores: {dist_rows[0]['distinct_norm']}")
        else:
            logger.warning(f"  No data for signal type: {sig_type}")
            results[sig_type] = {"signal_count": 0, "score_rows": 0}
    
    return results


def check_enrichment_timestamps():
    """Check when enrichments were last updated."""
    logger.info("\n" + "=" * 60)
    logger.info("CHECKING ENRICHMENT TIMESTAMPS")
    logger.info("=" * 60)
    
    sql = """
        SELECT 
            MIN(enriched_at) as first_enrichment,
            MAX(enriched_at) as last_enrichment,
            COUNT(*) as total_enriched
        FROM mcp_signal_enrichments
    """
    rows = ws_query(sql)
    
    if rows:
        r = rows[0]
        logger.info(f"  Total enriched rows: {r['total_enriched']}")
        logger.info(f"  First enrichment: {r['first_enrichment']}")
        logger.info(f"  Last enrichment: {r['last_enrichment']}")
    
    return rows[0] if rows else None


def check_signal_source_distribution():
    """Check distribution of signals by source."""
    logger.info("\n" + "=" * 60)
    logger.info("CHECKING SIGNAL SOURCE DISTRIBUTION")
    logger.info("=" * 60)
    
    sql = """
        SELECT signal_source, COUNT(*) as cnt
        FROM mcp_signal_enrichments
        GROUP BY signal_source
        ORDER BY cnt DESC
        LIMIT 20
    """
    rows = ws_query(sql)
    
    for row in rows:
        logger.info(f"  {row['signal_source']}: {row['cnt']}")
    
    return rows


def main():
    """Run weak signal discrimination investigation."""
    logger.info("=" * 60)
    logger.info("WEAK SIGNAL DISCRIMINATION INVESTIGATION")
    logger.info(f"Started at: {datetime.now(timezone.utc).isoformat()}")
    logger.info("=" * 60)
    
    target_fields = [
        'permission_scope',
        'temporal_stability', 
        'tool_description_safety'
    ]
    
    target_signal_types = [
        'permission_scope',
        'temporal_stability',
        'tool_description_safety'
    ]
    
    enrichment_results = analyze_signal_enrichments(target_fields)
    
    scores_results = analyze_signal_scores(target_signal_types)
    
    check_enrichment_timestamps()
    
    check_signal_source_distribution()
    
    logger.info("\n" + "=" * 60)
    logger.info("DIAGNOSTIC SUMMARY")
    logger.info("=" * 60)
    
    for field in target_fields:
        data = enrichment_results.get(field, {})
        distinct = data.get('distinct', 'N/A')
        logger.info(f"{field}: {distinct} distinct values")
    
    diagnostic_record = {
        "investigation_id": f"ws_inv_{datetime.now(timezone.utc).isoformat().replace(':', '').replace('-', '')}",
        "ts": datetime.now(timezone.utc).isoformat(),
        "target_fields": target_fields,
        "enrichment_analysis": enrichment_results,
        "scores_analysis": scores_results
    }
    
    logger.info("\nDiagnostic record prepared for audit logging")
    logger.info(f"Investigation complete at: {datetime.now(timezone.utc).isoformat()}")
    
    sys.exit(0)


if __name__ == "__main__":
    main()