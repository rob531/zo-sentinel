import sys
import os
import logging
import json
from datetime import timezone
from typing import Optional, Dict, Any, List

sys.path.insert(0, '/home/workspace/zo_sentinel')
from db_utils import ws_query, ws_write

SERVICE_NAME = 'signal_discrimination_diagnosis'
LOG_FILE = f'/home/workspace/logs/{SERVICE_NAME}.log'

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(name)s] %(levelname)s: %(message)s',
    handlers=[
        logging.FileHandler(LOG_FILE),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)


def count_distinct_scores_per_signal_type() -> Dict[str, Any]:
    """Count distinct score values per signal_type in mcp_signal_scores."""
    query = """
    SELECT 
        signal_type,
        COUNT(*) as total_rows,
        COUNT(DISTINCT score) as distinct_scores,
        MIN(score) as min_score,
        MAX(score) as max_score,
        AVG(score) as avg_score,
        LIST(DISTINCT score ORDER BY score) as all_distinct_values
    FROM mcp_signal_scores
    WHERE signal_type IN ('permission_scope', 'temporal_stability', 'tool_description_safety')
    GROUP BY signal_type
    ORDER BY signal_type
    """
    return ws_query(query)


def sample_rows_per_signal(target_count: int = 10) -> Dict[str, Any]:
    """Sample rows per signal showing score + evidence_blob."""
    query = f"""
    SELECT 
        signal_type,
        score,
        evidence_blob,
        computed_at,
        target_server_id
    FROM mcp_signal_scores
    WHERE signal_type IN ('permission_scope', 'temporal_stability', 'tool_description_safety')
    QUALIFY ROW_NUMBER() OVER (PARTITION BY signal_type ORDER BY computed_at DESC) <= {target_count}
    ORDER BY signal_type, computed_at DESC
    """
    return ws_query(query)


def check_enrichment_modules_called() -> Dict[str, Any]:
    """Check if enrichment modules are being called via mcp_signal_enrichments."""
    query = """
    SELECT 
        signal_type,
        COUNT(*) as total_enrichments,
        COUNT(DISTINCT server_id) as servers_enriched,
        MIN(enriched_at) as first_enrichment,
        MAX(enriched_at) as last_enrichment,
        COUNT(DISTINCT enrich_module) as distinct_modules
    FROM mcp_signal_enrichments
    WHERE signal_type IN ('permission_scope', 'temporal_stability', 'tool_description_safety')
    GROUP BY signal_type
    ORDER BY signal_type
    """
    return ws_query(query)


def check_enrichment_evidence_quality() -> Dict[str, Any]:
    """Sample enrichment evidence to see if fields are populated."""
    query = """
    SELECT 
        signal_type,
        enrich_module,
        evidence_blob,
        enriched_at
    FROM mcp_signal_enrichments
    WHERE signal_type IN ('permission_scope', 'temporal_stability', 'tool_description_safety')
    ORDER BY signal_type, enriched_at DESC
    LIMIT 30
    """
    return ws_query(query)


def check_signal_computation_sources() -> Dict[str, Any]:
    """Check if permission_scope etc. come from scoring or separate source tables."""
    query = """
    SELECT 
        signal_type,
        COUNT(*) as count,
        MIN(computed_at) as first_computed,
        MAX(computed_at) as last_computed
    FROM mcp_signal_scores
    WHERE signal_type LIKE 'permission_%'
       OR signal_type LIKE 'temporal_%'
       OR signal_type LIKE 'tool_description_%'
    GROUP BY signal_type
    ORDER BY signal_type
    """
    return ws_query(query)


def check_for_sub_signal_variants() -> Dict[str, Any]:
    """Check if there are sub-variants like permission_scope_v2 or similar."""
    query = """
    SELECT DISTINCT signal_type
    FROM mcp_signal_scores
    WHERE signal_type LIKE 'permission_%'
       OR signal_type LIKE 'temporal_%'
       OR signal_type LIKE 'tool_description_%'
    ORDER BY signal_type
    """
    return ws_query(query)


def run_diagnostics() -> Dict[str, Any]:
    """Run all diagnostics and compile results."""
    ts_now = datetime.now(timezone.utc).isoformat()
    
    logger.info("Starting signal discrimination diagnosis")
    
    results = {
        'diagnostic_run_at': ts_now,
        'signal_types_checked': ['permission_scope', 'temporal_stability', 'tool_description_safety'],
        'distinct_score_counts': {},
        'sample_rows': {},
        'enrichment_module_status': {},
        'enrichment_evidence_samples': {},
        'diagnosis': {}
    }
    
    try:
        distinct_counts = count_distinct_scores_per_signal_type()
        if distinct_counts and 'data' in distinct_counts:
            results['distinct_score_counts'] = distinct_counts['data']
            logger.info(f"Distinct score counts: {distinct_counts['data']}")
    except Exception as e:
        logger.error(f"Failed to count distinct scores: {e}")
        results['distinct_score_counts'] = {'error': str(e)}
    
    try:
        samples = sample_rows_per_signal()
        if samples and 'data' in samples:
            results['sample_rows'] = samples['data']
            logger.info(f"Retrieved {len(samples['data'])} sample rows")
    except Exception as e:
        logger.error(f"Failed to sample rows: {e}")
        results['sample_rows'] = {'error': str(e)}
    
    try:
        enrichment_status = check_enrichment_modules_called()
        if enrichment_status and 'data' in enrichment_status:
            results['enrichment_module_status'] = enrichment_status['data']
            logger.info(f"Enrichment module status: {enrichment_status['data']}")
    except Exception as e:
        logger.warning(f"Failed to check enrichment modules (table may not exist): {e}")
        results['enrichment_module_status'] = {'error': str(e)}
    
    try:
        evidence_quality = check_enrichment_evidence_quality()
        if evidence_quality and 'data' in evidence_quality:
            results['enrichment_evidence_samples'] = evidence_quality['data']
    except Exception as e:
        logger.warning(f"Failed to check enrichment evidence quality: {e}")
    
    try:
        sub_variants = check_for_sub_signal_variants()
        if sub_variants and 'data' in sub_variants:
            results['sub_signal_variants'] = sub_variants['data']
    except Exception as e:
        logger.warning(f"Failed to check sub-variants: {e}")
    
    diagnosis_notes = []
    if distinct_counts and 'data' in distinct_counts:
        for row in distinct_counts['data']:
            signal_type = row.get('signal_type', 'unknown')
            distinct_count = row.get('distinct_scores', 0)
            if distinct_count and distinct_count <= 4:
                diagnosis_notes.append(
                    f"ISSUE: {signal_type} has only {distinct_count} distinct score values - "
                    f"range [{row.get('min_score', '?')}, {row.get('max_score', '?')}]"
                )
            else:
                diagnosis_notes.append(
                    f"OK: {signal_type} has {distinct_count} distinct score values"
                )
    
    results['diagnosis'] = {
        'weak_signals': [],
        'notes': diagnosis_notes,
        'recommendation': ''
    }
    
    weak_count = sum(1 for n in diagnosis_notes if 'ISSUE' in n)
    if weak_count > 0:
        results['diagnosis']['recommendation'] = (
            f"Found {weak_count} signals with insufficient discrimination. "
            "Check if enrichment modules are producing normalized scores "
            "instead of raw computed values. Verify scoring functions in "
            "mcp_signal_scoring.py produce continuous value ranges."
        )
    else:
        results['diagnosis']['recommendation'] = (
            "All checked signals have multiple distinct score values. "
            "Discrimination appears adequate."
        )
    
    logger.info(f"Diagnosis complete: {diagnosis_notes}")
    
    return results


def persist_diagnostic_results(results: Dict[str, Any]) -> None:
    """Persist diagnostic results for historical tracking."""
    import hashlib
    diagnostic_id = hashlib.sha256(
        (results['diagnostic_run_at'] + SERVICE_NAME).encode()
    ).hexdigest()[:16]
    
    rows = [{
        'diagnostic_id': diagnostic_id,
        'diagnostic_service': SERVICE_NAME,
        'run_at': results['diagnostic_run_at'],
        'signal_types_checked': json.dumps(results['signal_types_checked']),
        'distinct_counts_json': json.dumps(results['distinct_score_counts']),
        'sample_count': len(results.get('sample_rows', {})),
        'enrichment_status_json': json.dumps(results['enrichment_module_status']),
        'diagnosis_json': json.dumps(results['diagnosis']),
        'weak_signal_count': len([n for n in results['diagnosis'].get('notes', []) if 'ISSUE' in n])
    }]
    
    try:
        ws_write('diagnostic_runs', rows)
        logger.info(f"Persisted diagnostic results with id: {diagnostic_id}")
    except Exception as e:
        logger.error(f"Failed to persist diagnostic results: {e}")


def main() -> None:
    """Main entry point for one-shot diagnostic run."""
    logger.info(f"Starting {SERVICE_NAME} diagnostic run")
    
    results = run_diagnostics()
    
    print("\n" + "="*80)
    print(f"SIGNAL DISCRIMINATION DIAGNOSIS - {results['diagnostic_run_at']}")
    print("="*80)
    
    print("\n### DISTINCT SCORE COUNTS PER SIGNAL ###")
    if results['distinct_score_counts']:
        for item in results['distinct_score_counts']:
            print(f"  {item['signal_type']}: {item['distinct_scores']} distinct "
                  f"(range {item['min_score']}-{item['max_score']}, avg {item['avg_score']:.2f})")
    
    print("\n### ENRICHMENT MODULE STATUS ###")
    if results['enrichment_module_status']:
        for item in results['enrichment_module_status']:
            print(f"  {item['signal_type']}: {item['total_enrichments']} enrichments, "
                  f"modules={item['distinct_modules']}")
    else:
        print("  (table mcp_signal_enrichments may not exist or is empty)")
    
    print("\n### DIAGNOSIS ###")
    for note in results['diagnosis'].get('notes', []):
        prefix = "  " + ("🔴" if "ISSUE" in note else "🟢")
        print(f"{prefix} {note}")
    
    print(f"\n### RECOMMENDATION ###")
    print(f"  {results['diagnosis'].get('recommendation', 'N/A')}")
    
    print("\n" + "="*80 + "\n")
    
    persist_diagnostic_results(results)
    
    logger.info("Diagnostic run completed successfully")
    sys.exit(0)


if __name__ == '__main__':
    main()