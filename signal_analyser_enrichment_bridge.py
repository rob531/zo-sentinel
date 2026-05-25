#!/usr/bin/env python3
import sys
sys.path.insert(0, '/home/workspace')
import os
import re
import time
from datetime import datetime, timezone
from typing import Dict, List, Any, Optional

SERVICE_NAME = 'signal_analyser_enrichment_bridge'
SERVICE_PORT = 0
WRITE_SERVICE_URL = 'http://127.0.0.1:8772'
QUERY_SERVICE_URL = 'http://127.0.0.1:8772'
EXECUTE_SERVICE_URL = 'http://127.0.0.1:8772'
QUERY_URL = f'{QUERY_SERVICE_URL}/query'
WRITE_URL = f'{WRITE_SERVICE_URL}/write'
EXECUTE_URL = f'{EXECUTE_SERVICE_URL}/execute'
LOG_FILE = '/home/workspace/logs/signal_analyser_enrichment_bridge.log'
SIGNAL_ANALYSER_PATH = '/home/workspace/zo_sentinel/signal_analyser.py'

WEAK_SIGNALS = ['permission_scope', 'temporal_stability', 'tool_description_safety']
SIGNAL_ENRICHMENTS_TABLE = 'mcp_signal_enrichments'
SIGNAL_SCORES_TABLE = 'mcp_signal_scores'

import logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(name)s] %(levelname)s: %(message)s',
    handlers=[logging.FileHandler(LOG_FILE)]
)
log = logging.getLogger(SERVICE_NAME)


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def ws_query(sql: str) -> List[Dict[str, Any]]:
    import requests
    resp = requests.post(QUERY_URL, json={'sql': sql}, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    return data.get('rows', [])


def ws_write(table: str, rows: List[Dict[str, Any]]) -> None:
    import requests
    requests.post(WRITE_URL, json={'table': table, 'rows': rows}, timeout=30)


def check_single_instance() -> bool:
    pid = str(os.getpid())
    pid_file = f'/tmp/{SERVICE_NAME}.pid'
    try:
        with open(pid_file, 'r') as f:
            existing = f.read().strip()
        if existing and existing != pid:
            log.error(f"Another instance running: {existing}. Exiting.")
            return False
    except FileNotFoundError:
        pass
    with open(pid_file, 'w') as f:
        f.write(pid)
    log.info(f"Running as PID {pid}")
    return True


def remove_pid_file() -> None:
    try:
        os.remove(f'/tmp/{SERVICE_NAME}.pid')
    except FileNotFoundError:
        pass


def signal_handler(signum: int, frame) -> None:
    log.warning(f"Signal {signum} received. Shutting down.")
    remove_pid_file()
    sys.exit(0)


def check_signal_analyser_source_for_enrichment_calls() -> Dict[str, Any]:
    result = {
        'queries_enrichments_table': False,
        'imports_enrichment_modules': False,
        'calls_compute_score': False,
        'enrichment_table_mentioned': False,
        'source_lines': 0,
    }
    try:
        with open(SIGNAL_ANALYSER_PATH, 'r') as f:
            source = f.read()
        result['source_lines'] = len(source.splitlines())
        result['queries_enrichments_table'] = 'mcp_signal_enrichments' in source
        result['enrichment_table_mentioned'] = result['queries_enrichments_table']
        result['imports_enrichment_modules'] = bool(
            re.search(r'import\s+(permission_scope|temporal_stability|tool_description)', source)
        )
        result['calls_compute_score'] = 'compute_score' in source
        log.info(f"Source scan: queries_enrichments={result['queries_enrichments_table']}, "
                f"imports_modules={result['imports_enrichment_modules']}, "
                f"calls_compute_score={result['calls_compute_score']}, "
                f"lines={result['source_lines']}")
    except Exception as e:
        log.error(f"Failed to read signal_analyser source: {e}")
    return result


def query_enrichment_counts() -> Dict[str, Dict[str, Any]]:
    counts = {}
    for sig in WEAK_SIGNALS:
        sql = f"""
        SELECT COUNT(*) as total_rows,
               COUNT(DISTINCT score) as distinct_scores,
               COUNT(CASE WHEN evidence IS NOT NULL THEN 1 END) as with_evidence,
               MIN(scored_at) as earliest,
               MAX(scored_at) as latest
        FROM mcp_signal_enrichments
        WHERE signal_type = '{sig}'
        """
        rows = ws_query(sql)
        if rows:
            counts[sig] = rows[0]
        else:
            counts[sig] = {'total_rows': 0, 'distinct_scores': 0, 'with_evidence': 0}
        log.info(f"Enrichment [{sig}]: {counts[sig]}")
    return counts


def query_signal_scores_counts() -> Dict[str, Dict[str, Any]]:
    counts = {}
    for sig in WEAK_SIGNALS:
        sql = f"""
        SELECT COUNT(*) as total_rows,
               COUNT(DISTINCT score) as distinct_scores,
               COUNT(CASE WHEN evidence IS NOT NULL THEN 1 END) as with_evidence,
               MIN(scored_at) as earliest,
               MAX(scored_at) as latest
        FROM mcp_signal_scores
        WHERE signal_name = '{sig}'
        """
        rows = ws_query(sql)
        if rows:
            counts[sig] = rows[0]
        else:
            counts[sig] = {'total_rows': 0, 'distinct_scores': 0, 'with_evidence': 0}
        log.info(f"SignalScores [{sig}]: {counts[sig]}")
    return counts


def query_enrichment_evidence_samples() -> Dict[str, List[Dict[str, Any]]]:
    samples = {}
    for sig in WEAK_SIGNALS:
        sql = f"""
        SELECT server_id, score, evidence
        FROM mcp_signal_enrichments
        WHERE signal_type = '{sig}'
        LIMIT 3
        """
        rows = ws_query(sql)
        samples[sig] = rows
        log.info(f"Enrichment samples [{sig}]: {len(rows)} rows returned")
    return samples


def query_signal_scores_samples() -> Dict[str, List[Dict[str, Any]]]:
    samples = {}
    for sig in WEAK_SIGNALS:
        sql = f"""
        SELECT server_id, score, evidence
        FROM mcp_signal_scores
        WHERE signal_name = '{sig}'
        ORDER BY computed_at DESC
        LIMIT 3
        """
        rows = ws_query(sql)
        samples[sig] = rows
        log.info(f"SignalScores samples [{sig}]: {len(rows)} rows returned")
    return samples


def compute_enrichment_score_distribution() -> Dict[str, Dict[str, int]]:
    distribution = {}
    for sig in WEAK_SIGNALS:
        sql = f"""
        SELECT score, COUNT(*) as cnt
        FROM mcp_signal_enrichments
        WHERE signal_type = '{sig}'
        GROUP BY score
        ORDER BY score
        """
        rows = ws_query(sql)
        distribution[sig] = {str(r['score']): r['cnt'] for r in rows}
        log.info(f"Enrichment distribution [{sig}]: {distribution[sig]}")
    return distribution


def compute_signal_scores_distribution() -> Dict[str, Dict[str, int]]:
    distribution = {}
    for sig in WEAK_SIGNALS:
        sql = f"""
        SELECT score, COUNT(*) as cnt
        FROM mcp_signal_scores
        WHERE signal_name = '{sig}'
        GROUP BY score
        ORDER BY score
        """
        rows = ws_query(sql)
        distribution[sig] = {str(r['score']): r['cnt'] for r in rows}
        log.info(f"SignalScores distribution [{sig}]: {distribution[sig]}")
    return distribution


def check_enrichment_evidence_blob_structure() -> Dict[str, Any]:
    results = {}
    for sig in WEAK_SIGNALS:
        sql = f"""
        SELECT server_id, evidence
        FROM mcp_signal_enrichments
        WHERE signal_type = '{sig}' AND evidence IS NOT NULL
        LIMIT 2
        """
        rows = ws_query(sql)
        blobs_ok = 0
        blobs_bad = 0
        for row in rows:
            ev = row.get('evidence', '')
            if ev:
                try:
                    import json
                    json.loads(str(ev))
                    blobs_ok += 1
                except Exception:
                    blobs_bad += 1
            else:
                blobs_bad += 1
        results[sig] = {'blobs_ok': blobs_ok, 'blobs_bad': blobs_bad}
        log.info(f"Evidence blob validity [{sig}]: ok={blobs_ok}, bad={blobs_bad}")
    return results


def find_servers_with_both_enrichment_and_signal_score() -> Dict[str, List[str]]:
    matching = {}
    for sig in WEAK_SIGNALS:
        sql_enrich = f"""
        SELECT server_id FROM mcp_signal_enrichments WHERE signal_type = '{sig}'
        """
        enrich_rows = ws_query(sql_enrich)
        enrich_ids = {r['server_id'] for r in enrich_rows}
        sql_score = f"""
        SELECT server_id FROM mcp_signal_scores WHERE signal_name = '{sig}'
        """
        score_rows = ws_query(sql_score)
        score_ids = {r['server_id'] for r in score_rows}
        both = enrich_ids & score_ids
        only_enrich = enrich_ids - score_ids
        only_score = score_ids - enrich_ids
        matching[sig] = list(both)[:5]
        log.info(f"Overlap [{sig}]: both={len(both)}, only_enrich={len(only_enrich)}, only_score={len(only_score)}")
        if len(only_enrich) > 0:
            log.warning(f"[{sig}] {len(only_enrich)} servers have enrichment but NO signal_scores row!")
        if len(only_score) > 0:
            log.info(f"[{sig}] {len(only_score)} servers have signal_scores row but NO enrichment row")
    return matching


def compute_correlation_between_enrichment_and_signal_scores() -> Dict[str, Any]:
    correlations = {}
    for sig in WEAK_SIGNALS:
        sql = f"""
        SELECT e.score as enrich_score, s.score as signal_score, COUNT(*) as cnt
        FROM mcp_signal_enrichments e
        JOIN mcp_signal_scores s ON e.server_id = s.server_id AND s.signal_name = '{sig}'
        WHERE e.signal_type = '{sig}'
        GROUP BY e.score, s.score
        ORDER BY e.score, s.score
        """
        rows = ws_query(sql)
        correlations[sig] = rows
        if rows:
            total = sum(r['cnt'] for r in rows)
            log.info(f"Correlation [{sig}] ({total} paired rows):")
            for r in rows:
                log.info(f"  enrich={r['enrich_score']} signal={r['signal_score']} count={r['cnt']}")
        else:
            log.warning(f"[{sig}] No paired rows found between enrichments and signal_scores!")
    return correlations


def check_schema_for_enrichments_table() -> Dict[str, Any]:
    sql = """
    SELECT column_name, data_type
    FROM information_schema.columns
    WHERE table_name = 'mcp_signal_enrichments'
    ORDER BY ordinal_position
    """
    rows = ws_query(sql)
    log.info(f"mcp_signal_enrichments schema: {[r['column_name'] for r in rows]}")
    return {r['column_name']: r['data_type'] for r in rows}


def check_schema_for_signal_scores_table() -> Dict[str, Any]:
    sql = """
    SELECT column_name, data_type
    FROM information_schema.columns
    WHERE table_name = 'mcp_signal_scores'
    ORDER BY ordinal_position
    """
    rows = ws_query(sql)
    log.info(f"mcp_signal_scores schema: {[r['column_name'] for r in rows]}")
    return {r['column_name']: r['data_type'] for r in rows}


def build_diagnostic_summary(
    source_check: Dict[str, Any],
    enrich_counts: Dict[str, Dict[str, Any]],
    score_counts: Dict[str, Dict[str, Any]],
    enrich_dist: Dict[str, Dict[str, int]],
    score_dist: Dict[str, Dict[str, int]],
    blob_check: Dict[str, Any],
    overlap: Dict[str, List[str]],
    correlations: Dict[str, Any],
    enrich_schema: Dict[str, Any],
    score_schema: Dict[str, Any],
) -> Dict[str, Any]:
    summary = {
        'ts': utc_now_iso(),
        'signal_analyser_calls_enrichments': source_check['queries_enrichments_table'],
        'signal_analyser_imports_modules': source_check['imports_enrichment_modules'],
        'signal_analyser_calls_compute_score': source_check['calls_compute_score'],
        'source_lines': source_check['source_lines'],
        'weak_signals_status': {},
        'overall_diagnosis': 'UNKNOWN',
        'recommendations': [],
    }
    signals_in_diagnosis = []
    signals_not_in_diagnosis = []
    for sig in WEAK_SIGNALS:
        ec = enrich_counts.get(sig, {})
        sc = score_counts.get(sig, {})
        ed = enrich_dist.get(sig, {})
        sd = score_dist.get(sig, {})
        bc = blob_check.get(sig, {})
        corr = correlations.get(sig, [])
        overlap_count = len(overlap.get(sig, []))
        total_enrich = ec.get('total_rows', 0)
        total_score = sc.get('total_rows', 0)
        distinct_enrich = ec.get('distinct_scores', 0)
        distinct_score = sc.get('distinct_scores', 0)
        status = 'UNKNOWN'
        issues = []
        if total_enrich == 0:
            status = 'NO_ENRICHMENT_DATA'
            issues.append(f"No rows in mcp_signal_enrichments for {sig}")
        elif total_score == 0:
            status = 'NO_SIGNAL_SCORES'
            issues.append(f"No rows in mcp_signal_scores for {sig}")
        elif distinct_score < 3 and distinct_enrich >= 3:
            status = 'ENRICHMENT_PRODUCING_BUT_NOT_CONSUMED'
            issues.append(f"Enrichment has {distinct_enrich} distinct scores but signal_scores only has {distinct_score}")
        elif distinct_enrich == 0 and total_enrich > 0:
            status = 'ENRICHMENT_ALL_SAME_SCORE'
            issues.append("All enrichment rows have same score")
        elif len(corr) == 0 and total_enrich > 0 and total_score > 0:
            status = 'NO_PAIRED_CORRELATION'
            issues.append("No joinable pairs between enrichments and signal_scores")
        elif distinct_score >= 3 and distinct_enrich >= 3:
            status = 'HEALTHY_BOTH_DIVERSE'
        else:
            status = 'NEEDS_INVESTIGATION'
        signals_in_diagnosis.append(sig) if status in ['HEALTHY_BOTH_DIVERSE'] else signals_not_in_diagnosis.append(sig)
        summary['weak_signals_status'][sig] = {
            'status': status,
            'enrichment_rows': total_enrich,
            'enrichment_distinct_scores': distinct_enrich,
            'enrichment_distribution': ed,
            'signal_scores_rows': total_score,
            'signal_scores_distinct_scores': distinct_score,
            'signal_scores_distribution': sd,
            'evidence_blobs_ok': bc.get('blobs_ok', 0),
            'evidence_blobs_bad': bc.get('blobs_bad', 0),
            'paired_overlap_count': overlap_count,
            'correlation_rows': len(corr),
            'issues': issues,
        }
    if all(s in signals_in_diagnosis for s in WEAK_SIGNALS):
        summary['overall_diagnosis'] = 'INTEGRATED'
        summary['recommendations'].append("All weak signals integrated: enrichments flow into signal_analyser correctly")
    else:
        if not source_check['queries_enrichments_table']:
            summary['overall_diagnosis'] = 'SOURCE_DOES_NOT_CALL_ENRICHMENTS_TABLE'
            summary['recommendations'].append("signal_analyser.py does NOT query mcp_signal_enrichments table. Add enrichment read + apply logic.")
        elif not source_check['imports_enrichment_modules']:
            summary['overall_diagnosis'] = 'SOURCE_DOES_NOT_IMPORT_ENRICHMENT_MODULES'
            summary['recommendations'].append("signal_analyser.py does NOT import enrichment modules. Import and call compute_score().")
        elif not source_check['calls_compute_score']:
            summary['overall_diagnosis'] = 'SOURCE_MISSING_COMPUTE_SCORE_CALL'
            summary['recommendations'].append("signal_analyser.py has enrichment refs but no compute_score() call pattern.")
        else:
            summary['overall_diagnosis'] = 'ENRICHMENTS_EXIST_BUT_NOT_APPLIED'
            summary['recommendations'].append("Enrichment rows exist but signal_analyser may not be applying them correctly.")
        for sig in signals_not_in_diagnosis:
            st = summary['weak_signals_status'][sig]
            if st['status'] == 'NO_SIGNAL_SCORES' and st['enrichment_rows'] > 0:
                summary['recommendations'].append(f"CRITICAL: {sig} has {st['enrichment_rows']} enrichment rows but 0 signal_scores rows. Bridge is broken.")
            elif st['status'] == 'ENRICHMENT_PRODUCING_BUT_NOT_CONSUMED':
                summary['recommendations'].append(f"CRITICAL: {sig} enrichment has {st['enrichment_distinct_scores']} scores but signal_scores only has {st['signal_scores_distinct_scores']}. signal_analyser not consuming enrichments.")
    return summary


def cycle() -> Dict[str, Any]:
    log.info("=" * 60)
    log.info("CYCLE START: signal_analyser_enrichment_bridge diagnostic")
    log.info("=" * 60)
    try:
        source_check = check_signal_analyser_source_for_enrichment_calls()
        enrich_counts = query_enrichment_counts()
        score_counts = query_signal_scores_counts()
        enrich_dist = compute_enrichment_score_distribution()
        score_dist = compute_signal_scores_distribution()
        blob_check = check_enrichment_evidence_blob_structure()
        overlap = find_servers_with_both_enrichment_and_signal_score()
        correlations = compute_correlation_between_enrichment_and_signal_scores()
        enrich_schema = check_schema_for_enrichments_table()
        score_schema = check_schema_for_signal_scores_table()
        summary = build_diagnostic_summary(
            source_check, enrich_counts, score_counts,
            enrich_dist, score_dist, blob_check, overlap,
            correlations, enrich_schema, score_schema
        )
        log.info("=" * 60)
        log.info(f"DIAGNOSIS: {summary['overall_diagnosis']}")
        for rec in summary['recommendations']:
            log.warning(f"RECOMMENDATION: {rec}")
        for sig in WEAK_SIGNALS:
            st = summary['weak_signals_status'][sig]
            log.info(f"  [{sig}] status={st['status']}, enrich_distinct={st['enrichment_distinct_scores']}, score_distinct={st['signal_scores_distinct_scores']}")
        log.info("=" * 60)
        return summary
    except Exception as e:
        log.error(f"Diagnostic cycle failed: {e}", exc_info=True)
        return {'error': str(e)}


def send_heartbeat(status: str = 'ok', meta: Optional[Dict[str, Any]] = None) -> None:
    row = {
        'service': SERVICE_NAME,
        'status': status,
        'ts': utc_now_iso(),
        'meta': meta or {},
    }
    try:
        requests.post(WRITE_URL, json={'table': 'service_health', 'rows': [row]}, timeout=10)
    except Exception as e:
        log.warning(f"Heartbeat failed: {e}")


def run() -> None:
    import signal
    if not check_single_instance():
        sys.exit(1)
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)
    log.info(f"{SERVICE_NAME} starting. Diagnostic probe for signal_analyser enrichment integration.")
    try:
        result = cycle()
        send_heartbeat('ok', {'diagnosis': result.get('overall_diagnosis', 'ERROR')})
        log.info("Diagnostic cycle complete. Exiting (one-shot diagnostic).")
    except Exception as e:
        log.error(f"Fatal error: {e}", exc_info=True)
        send_heartbeat('ERROR', {'error': str(e)})
    remove_pid_file()
    sys.exit(0)


if __name__ == '__main__':
    run()