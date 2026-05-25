import logging
from pathlib import Path
import requests
import json
from datetime import datetime, timezone

LOG_DIR = Path('/home/workspace/logs')  # <-- fixed: must be Path object, not str
LOG_DIR.mkdir(parents=True, exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(name)s] %(levelname)s: %(message)s',
    handlers=[logging.FileHandler(LOG_DIR / 'signal_discrimination_audit_v2.log')]
)
logger = logging.getLogger(__name__)

WRITE_SERVICE_URL = 'http://localhost:8772'
SERVICE_NAME = 'signal_discrimination_audit_v2'

ENRICHMENT_SCRIPT_MAP = {
    'permission_scope': '/home/workspace/zo_sentinel/permission_scope_enrichment_v2.py',
    'temporal_stability': '/home/workspace/zo_sentinel/temporal_stability_enrichment_v2.py',
    'tool_description_safety': '/home/workspace/zo_sentinel/tool_description_safety_enrichment_v2.py',
}


def ws_query(sql: str) -> list:
    resp = requests.post(f'{WRITE_SERVICE_URL}/query', json={'sql': sql}, timeout=30)
    resp.raise_for_status()
    result = resp.json()
    rows = result.get('rows', [])
    logger.debug("ws_query rows returned: %d", len(rows))
    return rows


def ws_write(table: str, rows: list) -> None:
    requests.post(f'{WRITE_SERVICE_URL}/write', json={'table': table, 'rows': rows}, timeout=30)


def send_heartbeat(status: str = 'running', meta: str = '') -> None:
    row = {
        'service': SERVICE_NAME,
        'last_heartbeat': datetime.now(timezone.utc).isoformat(),
        'status': status,
        'meta': meta,
    }
    ws_write('service_health', [row])


def load_enrichment_script(signal_name: str) -> str:
    path = Path(ENRICHMENT_SCRIPT_MAP.get(signal_name, ''))
    if path.exists():
        return path.read_text()
    return ''


def audit_discrimination() -> dict:
    logger.info("Querying distinct value counts per signal_name from mcp_signal_enrichments")
    sql = """
        SELECT
            signal_name,
            COUNT(DISTINCT server_id) as server_count,
            COUNT(DISTINCT score::STRING) as distinct_scores,
            COUNT(DISTINCT verdict) as distinct_verdicts,
            MIN(score) as min_score,
            MAX(score) as max_score,
            AVG(score) as avg_score
        FROM mcp_signal_enrichments
        WHERE score IS NOT NULL
        GROUP BY signal_name
        ORDER BY signal_name
    """
    rows = ws_query(sql)

    discrimination_report = {
        'audit_ts': datetime.now(timezone.utc).isoformat(),
        'signals': [],
        'weak_signals': [],
        'recommendations': [],
    }

    for row in rows:
        signal = row.get('signal_name', '')
        distinct_scores = int(row.get('distinct_scores', 0))
        distinct_verdicts = int(row.get('distinct_verdicts', 0))
        server_count = int(row.get('server_count', 0))
        min_score = float(row.get('min_score', 0))
        max_score = float(row.get('max_score', 0))
        avg_score = float(row.get('avg_score', 0))

        sig_record = {
            'signal_name': signal,
            'server_count': server_count,
            'distinct_scores': distinct_scores,
            'distinct_verdicts': distinct_verdicts,
            'min_score': round(min_score, 4),
            'max_score': round(max_score, 4),
            'avg_score': round(avg_score, 4),
            'is_weak': distinct_scores <= 4,
        }
        discrimination_report['signals'].append(sig_record)

        if distinct_scores <= 4:
            discrimination_report['weak_signals'].append(sig_record)
            logger.warning(
                "Weak signal detected: %s | distinct_scores=%d | server_count=%d | range=[%.4f, %.4f]",
                signal, distinct_scores, server_count, min_score, max_score
            )

    # Specific formula audits for the three flagged scripts
    for sig_key, script_path in ENRICHMENT_SCRIPT_MAP.items():
        script_text = load_enrichment_script(sig_key)
        if not script_text:
            logger.warning("Enrichment script not found: %s", script_path)
            continue

        sig_record = next((s for s in discrimination_report['signals'] if sig_key in s['signal_name']), None)
        if not sig_record:
            continue

        recommendations = []

        # permission_scope: should have wide score range (read/write/admin scopes)
        if sig_key == 'permission_scope':
            if sig_record['distinct_scores'] <= 4:
                recommendations.append({
                    'issue': f"Flat score distribution ({sig_record['distinct_scores']} distinct values)",
                    'root_cause': 'compute_score() may be using hard-coded bucket thresholds that collapse distinct permission sets',
                    'fix': 'Expand bucket definitions to distinguish read_vs_write_vs_admin vs read-only vs no-permissions; consider weighting each permission bit independently before summing',
                    'verify': 'SELECT signal_name, score, COUNT(*) FROM mcp_signal_enrichments WHERE signal_name LIKE "%permission_scope%" GROUP BY signal_name, score ORDER BY score'
                })

        # temporal_stability: should reflect drift between first_seen/last_seen
        if sig_key == 'temporal_stability':
            if sig_record['distinct_scores'] <= 4:
                recommendations.append({
                    'issue': f"Flat score distribution ({sig_record['distinct_scores']} distinct values)",
                    'root_cause': 'compute_score() may be using coarse age buckets (e.g., <30d/30-90d/>90d) with no interpolation',
                    'fix': 'Replace bucket thresholds with continuous decay function: score = 1 - exp(-days_since_first_seen / half_life_days); calibrate half_life at 90 days',
                    'verify': 'SELECT signal_name, score, COUNT(*) FROM mcp_signal_enrichments WHERE signal_name LIKE "%temporal_stability%" GROUP BY signal_name, score ORDER BY score'
                })

        # tool_description_safety: should capture LLM-safety signal from tool description content
        if sig_key == 'tool_description_safety':
            if sig_record['distinct_scores'] <= 4:
                recommendations.append({
                    'issue': f"Flat score distribution ({sig_record['distinct_scores']} distinct values)",
                    'root_cause': 'compute_score() may be applying binary safe/unsafe flag rather than continuous safety probability',
                    'fix': 'Replace binary threshold with safety_score = sigmoid(llm_safety_logit) for continuous 0-1 output; if no LLM call is made, use heuristic weighted keyword scoring across description tokens',
                    'verify': 'SELECT signal_name, score, COUNT(*) FROM mcp_signal_enrichments WHERE signal_name LIKE "%tool_description_safety%" GROUP BY signal_name, score ORDER BY score'
                })

        if recommendations:
            discrimination_report['recommendations'].append({
                'signal': sig_key,
                'script': script_path,
                'items': recommendations
            })

    return discrimination_report


def main() -> None:
    logger.info("=== Signal Discrimination Audit v2 started ===")
    send_heartbeat(status='running')

    report = audit_discrimination()

    output_path = Path('/home/workspace/zo_sentinel/signal_discrimination_report_v2.json')
    output_path.write_text(json.dumps(report, indent=2))
    logger.info("Report written to %s", output_path)

    logger.info("=== Summary ===")
    logger.info("Total signals audited: %d", len(report['signals']))
    logger.info("Weak signals (<=4 distinct scores): %d", len(report['weak_signals']))
    for sig in report['signals']:
        flag = ' [WEAK]' if sig['is_weak'] else ''
        logger.info(
            "  %s: servers=%d distinct_scores=%d range=[%.4f, %.4f] avg=%.4f%s",
            sig['signal_name'], sig['server_count'], sig['distinct_scores'],
            sig['min_score'], sig['max_score'], sig['avg_score'], flag
        )

    logger.info("=== Recommendations ===")
    for rec_group in report['recommendations']:
        logger.info("Signal: %s", rec_group['signal'])
        for item in rec_group['items']:
            logger.info("  issue: %s", item['issue'])
            logger.info("  root_cause: %s", item['root_cause'])
            logger.info("  fix: %s", item['fix'])

    send_heartbeat(status='ok', meta=f"audited={len(report['signals'])} weak={len(report['weak_signals'])}")
    logger.info("=== Signal Discrimination Audit v2 complete ===")


if __name__ == '__main__':
    main()