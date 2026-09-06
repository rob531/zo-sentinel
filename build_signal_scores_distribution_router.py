import logging
import sys
from datetime import datetime, timezone
from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse

WRITE_SERVICE_URL = 'http://localhost:8772'
QUERY_URL = 'http://localhost:8772/query'
EXECUTE_URL = 'http://localhost:8772/execute'

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(name)s] %(levelname)s: %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)
log = logging.getLogger('signal_scores_distribution_router')

router = APIRouter(prefix='/api/signal-scores/distribution', tags=['signal-scores-distribution'])


def utc_now_iso():
    return datetime.now(timezone.utc).isoformat()


def ws_query(sql: str):
    import requests
    try:
        resp = requests.post(QUERY_URL, json={'sql': sql}, timeout=15)
        resp.raise_for_status()
        return resp.json().get('rows', [])
    except Exception as e:
        log.error('ws_query failed: %s', e)
        return []


def ws_write(table: str, rows: list):
    import requests
    try:
        resp = requests.post(WRITE_SERVICE_URL, json={'table': table, 'rows': rows, 'wait': True}, timeout=15)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        log.error('ws_write failed: %s', e)
        return {'ok': False}


def get_signal_names() -> list[str]:
    sql = "SELECT DISTINCT signal_name FROM mcp_signal_scores ORDER BY signal_name"
    rows = ws_query(sql)
    return [r.get('signal_name') for r in rows if r.get('signal_name')]


def get_overall_distribution():
    sql = """
    SELECT
        signal_name,
        COUNT(*) as total_count,
        COUNT(DISTINCT server_id) as unique_servers,
        AVG(score) as avg_score,
        MIN(score) as min_score,
        MAX(score) as max_score,
        STDDEV_SAMP(score) as stddev_score
    FROM mcp_signal_scores
    GROUP BY signal_name
    ORDER BY signal_name
    """
    return ws_query(sql)


def get_score_buckets(signal_name: str, buckets: int = 10):
    min_sql = f"SELECT MIN(score) as mn, MAX(score) as mx FROM mcp_signal_scores WHERE signal_name = '{signal_name}'"
    range_rows = ws_query(min_sql)
    if not range_rows:
        return []
    mn = range_rows[0].get('mn', 0.0) or 0.0
    mx = range_rows[0].get('mx', 1.0) or 1.0
    if mx <= mn:
        mx = mn + 1.0
    bucket_width = (mx - mn) / buckets
    results = []
    for i in range(buckets):
        lo = mn + i * bucket_width
        hi = mn + (i + 1) * bucket_width
        count_sql = f"""
        SELECT COUNT(*) as cnt FROM mcp_signal_scores
        WHERE signal_name = '{signal_name}'
        AND score >= {lo} AND score < {hi}
        """
        cnt_rows = ws_query(count_sql)
        cnt = cnt_rows[0].get('cnt', 0) if cnt_rows else 0
        results.append({
            'bucket': i + 1,
            'range_low': round(lo, 4),
            'range_high': round(hi, 4),
            'count': cnt
        })
    return results


def get_verdict_distribution():
    sql = """
    SELECT
        r.verdict,
        COUNT(DISTINCT ss.server_id) as server_count,
        AVG(ss.score) as avg_score
    FROM mcp_signal_scores ss
    JOIN mcp_server_registry r ON r.server_id = ss.server_id
    GROUP BY r.verdict
    ORDER BY r.verdict
    """
    return ws_query(sql)


def get_percentiles(signal_name: str):
    percentiles = [10, 25, 50, 75, 90, 95, 99]
    results = []
    for p in percentiles:
        sql = f"""
        SELECT score FROM mcp_signal_scores
        WHERE signal_name = '{signal_name}'
        ORDER BY score
        LIMIT 1 OFFSET (
            SELECT CAST(COUNT(*) * {p} / 100.0 AS BIGINT)
            FROM mcp_signal_scores
            WHERE signal_name = '{signal_name}'
        )
        """
        rows = ws_query(sql)
        results.append({
            'percentile': p,
            'value': rows[0].get('score') if rows else None
        })
    return results


@router.get('/overview')
def get_distribution_overview():
    log.info('GET /api/signal-scores/distribution/overview')
    overall = get_overall_distribution()
    verdict_dist = get_verdict_distribution()
    signal_names = get_signal_names()
    return JSONResponse({
        'ts': utc_now_iso(),
        'overall': overall,
        'by_verdict': verdict_dist,
        'signal_count': len(signal_names),
        'signal_names': signal_names
    })


@router.get('/signal/{signal_name}')
def get_signal_distribution(
    signal_name: str,
    buckets: int = Query(default=10, ge=3, le=50)
):
    log.info('GET /api/signal-scores/distribution/signal/%s buckets=%d', signal_name, buckets)
    distribution = get_score_buckets(signal_name, buckets)
    percentiles = get_percentiles(signal_name)
    summary_sql = f"""
    SELECT
        COUNT(*) as total_count,
        COUNT(DISTINCT server_id) as unique_servers,
        AVG(score) as avg_score,
        MIN(score) as min_score,
        MAX(score) as max_score
    FROM mcp_signal_scores
    WHERE signal_name = '{signal_name}'
    """
    summary_rows = ws_query(summary_sql)
    summary = summary_rows[0] if summary_rows else {}
    return JSONResponse({
        'ts': utc_now_iso(),
        'signal_name': signal_name,
        'summary': summary,
        'buckets': distribution,
        'percentiles': percentiles
    })


@router.get('/all-signals')
def get_all_signals_distribution():
    log.info('GET /api/signal-scores/distribution/all-signals')
    signal_names = get_signal_names()
    results = []
    for sig in signal_names:
        sql = f"""
        SELECT
            signal_name,
            COUNT(*) as total_count,
            COUNT(DISTINCT server_id) as unique_servers,
            AVG(score) as avg_score,
            MIN(score) as min_score,
            MAX(score) as max_score
        FROM mcp_signal_scores
        WHERE signal_name = '{sig}'
        """
        rows = ws_query(sql)
        if rows:
            results.append(rows[0])
    return JSONResponse({
        'ts': utc_now_iso(),
        'signals': results
    })


@router.get('/verdict-breakdown')
def get_verdict_breakdown():
    log.info('GET /api/signal-scores/distribution/verdict-breakdown')
    verdict_dist = get_verdict_distribution()
    return JSONResponse({
        'ts': utc_now_iso(),
        'by_verdict': verdict_dist
    })


@router.get('/histogram/{signal_name}')
def get_signal_histogram(
    signal_name: str,
    bucket_count: int = Query(default=20, ge=5, le=100)
):
    log.info('GET /api/signal-scores/distribution/histogram/%s bucket_count=%d', signal_name, bucket_count)
    buckets = get_score_buckets(signal_name, bucket_count)
    total = sum(b.get('count', 0) for b in buckets)
    normalized = []
    for b in buckets:
        pct = (b['count'] / total * 100.0) if total > 0 else 0.0
        normalized.append({**b, 'percentage': round(pct, 2)})
    return JSONResponse({
        'ts': utc_now_iso(),
        'signal_name': signal_name,
        'bucket_count': bucket_count,
        'total_samples': total,
        'histogram': normalized
    })


if __name__ == '__main__':
    import uvicorn
    from fastapi import FastAPI
    app = FastAPI(title='signal_scores_distribution_router')
    app.include_router(router)
    uvicorn.run(app, host='0.0.0.0', port=8784)