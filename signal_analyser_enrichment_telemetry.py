import logging
import sys
import os
import hashlib
import json
import datetime
import inspect
import ast
from pathlib import Path
from typing import Any, Dict, List, Optional
from datetime import timezone

WRITE_SERVICE_URL = 'http://localhost:8772'
SERVICE_NAME = 'signal_analyser_enrichment_telemetry'

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(name)s] %(levelname)s: %(message)s',
    handlers=[
        logging.FileHandler(f'/home/workspace/logs/{SERVICE_NAME}.log'),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)


def ws_write(table: str, rows: List[Dict[str, Any]]) -> Optional[Dict]:
    """Write rows to DuckDB via write_service."""
    import requests
    payload = {'table': table, 'rows': rows, 'wait': True}
    try:
        resp = requests.post(WRITE_SERVICE_URL + '/write', json=payload, timeout=30)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        logger.error(f"ws_write failed for {table}: {e}")
        return None


def ws_query(sql: str) -> Optional[List[Dict[str, Any]]]:
    """Query DuckDB via write_service."""
    import requests
    payload = {'sql': sql, 'wait': True}
    try:
        resp = requests.post(WRITE_SERVICE_URL + '/query', json=payload, timeout=30)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        logger.error(f"ws_query failed: {e}")
        return None


def compute_deterministic_id(content: Dict[str, Any]) -> str:
    """Generate deterministic MD5 hash for idempotent writes."""
    canonical = json.dumps(content, sort_keys=True)
    return hashlib.md5(canonical.encode()).hexdigest()


def send_heartbeat(status: str = 'running', meta: Optional[Dict] = None) -> None:
    """Send heartbeat to service_health table."""
    rows = [{
        'service_name': SERVICE_NAME,
        'status': status,
        'last_heartbeat': datetime.now(timezone.utc).isoformat(),
        'meta': json.dumps(meta) if meta else '{}'
    }]
    ws_write('service_health', rows)


def introspect_signal_analyser() -> Dict[str, Any]:
    """Read signal_analyser.py to understand enrichment call patterns."""
    signal_analyser_path = Path('/home/workspace/zo_sentinel/signal_analyser.py')
    if not signal_analyser_path.exists():
        logger.warning("signal_analyser.py not found at expected path")
        return {'found': False, 'enrichment_calls': []}
    
    with open(signal_analyser_path, 'r') as f:
        source = f.read()
    
    try:
        tree = ast.parse(source)
    except SyntaxError as e:
        logger.error(f"Failed to parse signal_analyser.py: {e}")
        return {'found': True, 'parse_error': str(e), 'enrichment_calls': []}
    
    enrichment_calls = []
    enrichment_imports = []
    
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if node.module and 'enrich' in node.module.lower():
                for alias in node.names:
                    enrichment_imports.append({
                        'module': node.module,
                        'name': alias.name,
                        'asname': alias.asname
                    })
        elif isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name):
                func_name = node.func.id
                if 'enrich' in func_name.lower():
                    args_info = [f'{arg.value}' if isinstance(arg, ast.Constant) else 'dynamic' 
                                for arg in node.args[:3]]
                    enrichment_calls.append({
                        'function': func_name,
                        'args_preview': args_info,
                        'line': node.lineno
                    })
            elif isinstance(node.func, ast.Attribute):
                attr_name = node.func.attr
                if 'enrich' in attr_name.lower():
                    args_info = [f'{arg.value}' if isinstance(arg, ast.Constant) else 'dynamic' 
                                for arg in node.args[:3]]
                    enrichment_calls.append({
                        'function': attr_name,
                        'args_preview': args_info,
                        'line': node.lineno
                    })
    
    return {
        'found': True,
        'source_length': len(source.splitlines()),
        'enrichment_imports': enrichment_imports,
        'enrichment_calls': enrichment_calls
    }


def check_enrichment_coverage() -> Dict[str, Any]:
    """Check coverage gap between signal_scores and enrichments."""
    scores_query = """
    SELECT COUNT(*) as total_scores
    FROM mcp_signal_scores
    """
    enrichments_query = """
    SELECT COUNT(DISTINCT signal_id) as enriched_signals
    FROM mcp_signal_enrichments
    """
    
    scores_result = ws_query(scores_query)
    enrichments_result = ws_query(enrichments_query)
    
    total_scores = 0
    enriched_signals = 0
    
    if scores_result and len(scores_result) > 0:
        total_scores = scores_result[0].get('total_scores', 0)
    if enrichments_result and len(enrichments_result) > 0:
        enriched_signals = enrichments_result[0].get('enriched_signals', 0)
    
    coverage_pct = (enriched_signals / total_scores * 100) if total_scores > 0 else 0
    
    return {
        'total_scores': total_scores,
        'enriched_signals': enriched_signals,
        'coverage_pct': round(coverage_pct, 2),
        'gap': total_scores - enriched_signals
    }


def check_enrichment_table_schema() -> List[Dict[str, Any]]:
    """Introspect mcp_signal_enrichments schema."""
    schema_query = """
    SELECT column_name, data_type, is_nullable
    FROM information_schema.columns
    WHERE table_name = 'mcp_signal_enrichments'
    ORDER BY ordinal_position
    """
    result = ws_query(schema_query)
    return result if result else []


def log_telemetry(
    telemetry_type: str,
    enrichment_module: str,
    signal_id: str,
    metadata_fields: List[str],
    returned_score: Optional[float],
    timestamp: str,
    success: bool,
    error_message: Optional[str] = None
) -> None:
    """Log enrichment telemetry event."""
    telemetry_id = compute_deterministic_id({
        'telemetry_type': telemetry_type,
        'enrichment_module': enrichment_module,
        'signal_id': signal_id,
        'timestamp': timestamp
    })
    
    row = {
        'telemetry_id': telemetry_id,
        'telemetry_type': telemetry_type,
        'enrichment_module': enrichment_module,
        'signal_id': signal_id,
        'metadata_fields': json.dumps(metadata_fields),
        'metadata_field_count': len(metadata_fields),
        'returned_score': returned_score,
        'success': success,
        'error_message': error_message,
        'logged_at': timestamp,
        'source': 'signal_analyser_enrichment_telemetry'
    }
    
    ws_write('mcp_signal_enrichment_telemetry', [row])


def create_telemetry_table() -> None:
    """Create telemetry table if it doesn't exist."""
    create_sql = """
    CREATE TABLE IF NOT EXISTS mcp_signal_enrichment_telemetry (
        telemetry_id VARCHAR PRIMARY KEY,
        telemetry_type VARCHAR,
        enrichment_module VARCHAR,
        signal_id VARCHAR,
        metadata_fields JSON,
        metadata_field_count INTEGER,
        returned_score DOUBLE,
        success BOOLEAN,
        error_message VARCHAR,
        logged_at TIMESTAMPTZ,
        source VARCHAR
    )
    """
    ws_write('_internal', [{'sql': create_sql}])
    logger.info("Telemetry table ensured")


def probe_enrichment_wiring() -> Dict[str, Any]:
    """Probe the actual enrichment wiring patterns."""
    coverage = check_enrichment_coverage()
    schema = check_enrichment_table_schema()
    introspect = introspect_signal_analyser()
    
    recent_enrichments_query = """
    SELECT 
        signal_id,
        enrichment_module,
        metadata_fields,
        returned_score,
        enriched_at
    FROM mcp_signal_enrichments
    ORDER BY enriched_at DESC
    LIMIT 20
    """
    recent = ws_query(recent_enrichments_query)
    
    return {
        'coverage': coverage,
        'schema': schema,
        'signal_analyser_introspection': introspect,
        'recent_enrichment_samples': recent[:5] if recent else []
    }


def cycle() -> Dict[str, Any]:
    """Perform one cycle of telemetry verification."""
    logger.info("Starting enrichment telemetry verification cycle")
    
    create_telemetry_table()
    
    probe_result = probe_enrichment_wiring()
    
    coverage = probe_result['coverage']
    logger.info(f"Enrichment coverage: {coverage['enriched_signals']}/{coverage['total_scores']} "
                f"({coverage['coverage_pct']}% covered, {coverage['gap']} gap)")
    
    introspect = probe_result['signal_analyser_introspection']
    if introspect.get('found'):
        logger.info(f"signal_analyser.py: {introspect.get('source_length', 0)} lines")
        logger.info(f"Enrichment imports: {introspect.get('enrichment_imports', [])}")
        logger.info(f"Enrichment calls found: {len(introspect.get('enrichment_calls', []))}")
    
    schema = probe_result['schema']
    logger.info(f"mcp_signal_enrichments schema: {[s.get('column_name') for s in schema]}")
    
    recent = probe_result.get('recent_enrichment_samples', [])
    for sample in recent:
        meta_fields = []
        try:
            meta_raw = sample.get('metadata_fields', '{}')
            if isinstance(meta_raw, str):
                meta_parsed = json.loads(meta_raw)
            elif isinstance(meta_raw, dict):
                meta_parsed = meta_raw
            else:
                meta_parsed = {}
            meta_fields = list(meta_parsed.keys())
        except (json.JSONDecodeError, TypeError):
            meta_fields = ['parse_error']
        
        log_telemetry(
            telemetry_type='verification_sample',
            enrichment_module=sample.get('enrichment_module', 'unknown'),
            signal_id=sample.get('signal_id', ''),
            metadata_fields=meta_fields,
            returned_score=sample.get('returned_score'),
            timestamp=sample.get('enriched_at', datetime.now(timezone.utc).isoformat()),
            success=True
        )
    
    logger.info(f"Logged {len(recent)} telemetry samples")
    
    send_heartbeat(status='completed', meta={
        'coverage_pct': coverage['coverage_pct'],
        'gap': coverage['gap'],
        'enrichment_calls_found': len(introspect.get('enrichment_calls', []))
    })
    
    return probe_result


def run():
    """Entry point for running telemetry verification."""
    logger.info(f"{SERVICE_NAME} starting")
    result = cycle()
    logger.info(f"{SERVICE_NAME} completed successfully")
    return result


if __name__ == '__main__':
    result = run()
    print(json.dumps(result, indent=2, default=str))
    sys.exit(0)