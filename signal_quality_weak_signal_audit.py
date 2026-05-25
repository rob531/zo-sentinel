import logging
import os
import sys
import hashlib
from datetime import datetime, timezone

import requests

SERVICE_NAME = 'signal_quality_weak_signal_audit'
WRITE_SERVICE_URL = 'http://localhost:8772'

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(name)s] %(levelname)s: %(message)s',
    handlers=[logging.FileHandler(f'/home/workspace/logs/{SERVICE_NAME}.log')]
)
logger = logging.getLogger(__name__)


def ws_write(table, rows):
    """Helper to write rows via write_service."""
    payload = {'table': table, 'rows': rows, 'wait': True}
    resp = requests.post(WRITE_SERVICE_URL + '/write', json=payload, timeout=30)
    resp.raise_for_status()
    return resp.json()


def ws_query(sql, params=None):
    """Helper to query via write_service."""
    payload = {'sql': sql, 'params': params or {}}
    resp = requests.post(WRITE_SERVICE_URL + '/query', json=payload, timeout=30)
    resp.raise_for_status()
    return resp.json()


def get_schema_columns(table_name):
    """Introspect live schema for a table."""
    sql = """
        SELECT column_name, data_type
        FROM information_schema.columns
        WHERE table_name = ?
        ORDER BY ordinal_position
    """
    result = ws_query(sql, [table_name])
    return {row['column_name']: row['data_type'] for row in result}


def audit_weak_signals():
    """Audit three weak signals: permission_scope, temporal_stability, tool_description_safety."""
    weak_signals = ['permission_scope', 'temporal_stability', 'tool_description_safety']
    now_iso = datetime.now(timezone.utc).isoformat()

    logger.info("Starting weak signal audit")

    schema = get_schema_columns('mcp_signal_scores')
    logger.info(f"mcp_signal_scores schema: {schema}")

    has_version_col = 'version' in schema
    has_v2_enriched = 'v2_enriched' in schema
    has_enrichment_version = 'enrichment_version' in schema

    logger.info(f"Has version column: {has_version_col}, v2_enriched: {has_v2_enriched}, enrichment_version: {has_enrichment_version}")

    audit_id = hashlib.md5(f"weak_signal_audit_{now_iso}".encode()).hexdigest()[:16]
    audit_rows = []

    for signal in weak_signals:
        signal_id = hashlib.md5(signal.encode()).hexdigest()[:16]

        count_sql = f"SELECT COUNT(*) as total FROM mcp_signal_scores WHERE signal_name = ?"
        count_result = ws_query(count_sql, [signal])
        total = count_result[0]['total'] if count_result else 0

        distinct_sql = f"SELECT COUNT(DISTINCT {signal}) as distinct_values FROM mcp_signal_scores WHERE signal_name = ?"
        distinct_result = ws_query(distinct_sql, [signal])
        distinct_count = distinct_result[0]['distinct_values'] if distinct_result else 0

        cardinality_pct = (distinct_count / total * 100) if total > 0 else 0

        logger.info(f"Signal '{signal}': total={total}, distinct={distinct_count}, cardinality_pct={cardinality_pct:.2f}%")

        v1_sql = f"""
            SELECT COUNT(*) as v1_count,
                   COALESCE(AVG(score_value), 0) as v1_avg_score,
                   COUNT(DISTINCT {signal}) as v1_distinct
            FROM mcp_signal_scores
            WHERE signal_name = ?
              AND (version = 'v1' OR v2_enriched = FALSE OR enrichment_version = 'v1' OR enrichment_version IS NULL)
        """

        v2_sql = f"""
            SELECT COUNT(*) as v2_count,
                   COALESCE(AVG(score_value), 0) as v2_avg_score,
                   COUNT(DISTINCT {signal}) as v2_distinct
            FROM mcp_signal_scores
            WHERE signal_name = ?
              AND (version = 'v2' OR v2_enriched = TRUE OR enrichment_version = 'v2')
        """

        v1_result = ws_query(v1_sql, [signal])
        v2_result = ws_query(v2_sql, [signal])

        v1_count = v1_result[0]['v1_count'] if v1_result else 0
        v1_avg = v1_result[0]['v1_avg_score'] if v1_result else 0.0
        v1_distinct = v1_result[0]['v1_distinct'] if v1_result else 0

        v2_count = v2_result[0]['v2_count'] if v2_result else 0
        v2_avg = v2_result[0]['v2_avg_score'] if v2_result else 0.0
        v2_distinct = v2_result[0]['v2_distinct'] if v2_result else 0

        v2_higher_discrimination = v2_distinct > v1_distinct if (v1_distinct > 0 and v2_distinct > 0) else None

        logger.info(f"  v1: count={v1_count}, avg_score={v1_avg:.4f}, distinct={v1_distinct}")
        logger.info(f"  v2: count={v2_count}, avg_score={v2_avg:.4f}, distinct={v2_distinct}")
        logger.info(f"  v2_higher_discrimination: {v2_higher_discrimination}")

        row = {
            'audit_id': audit_id,
            'audit_ts': now_iso,
            'signal_name': signal,
            'signal_id': signal_id,
            'total_records': total,
            'distinct_values': distinct_count,
            'cardinality_pct': round(cardinality_pct, 2),
            'v1_count': v1_count,
            'v1_avg_score': round(v1_avg, 4),
            'v1_distinct': v1_distinct,
            'v2_count': v2_count,
            'v2_avg_score': round(v2_avg, 4),
            'v2_distinct': v2_distinct,
            'v2_higher_discrimination': str(v2_higher_discrimination) if v2_higher_discrimination is not None else 'unknown',
        }
        audit_rows.append(row)

    ws_write('signal_quality_audit_log', audit_rows)
    logger.info(f"Audit complete. audit_id={audit_id}, rows_written={len(audit_rows)}")

    return audit_id, audit_rows


if __name__ == '__main__':
    try:
        audit_id, rows = audit_weak_signals()
        logger.info(f"SUCCESS: audit_id={audit_id}")
        for r in rows:
            logger.info(f"  {r['signal_name']}: v2_higher_discrimination={r['v2_higher_discrimination']}")
        sys.exit(0)
    except Exception as e:
        logger.error(f"AUDIT FAILED: {e}")
        sys.exit(1)