import ast
import os
import sys
import logging
import requests
from datetime import datetime, timezone
from typing import Optional, Dict, Any, List

SERVICE_NAME = 'supply_chain_enrichment_wiring_check'
WRITE_SERVICE_URL = 'http://localhost:8772'
LOG_FILE = f'/home/workspace/logs/{SERVICE_NAME}.log'

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(name)s] %(levelname)s: %(message)s',
    handlers=[logging.FileHandler(LOG_FILE)]
)
logger = logging.getLogger(__name__)


def ws_query(sql: str, params: Optional[tuple] = None) -> List[Dict[str, Any]]:
    """Query DuckDB via write_service."""
    payload = {'sql': sql}
    if params:
        payload['params'] = params
    try:
        resp = requests.post(
            f'{WRITE_SERVICE_URL}/query',
            json=payload,
            timeout=30
        )
        resp.raise_for_status()
        result = resp.json()
        return result.get('rows', [])
    except Exception as e:
        logger.error(f"ws_query failed: {e}")
        return []


def ws_write(table: str, rows: List[Dict[str, Any]]) -> bool:
    """Write to DuckDB via write_service."""
    try:
        resp = requests.post(
            f'{WRITE_SERVICE_URL}/write',
            json={'table': table, 'rows': rows, 'wait': True},
            timeout=30
        )
        resp.raise_for_status()
        return True
    except Exception as e:
        logger.error(f"ws_write failed: {e}")
        return False


def send_heartbeat(status: str, meta: Dict[str, Any]) -> None:
    """Send heartbeat to service_health table."""
    ts = datetime.now(timezone.utc).isoformat()
    rows = [{
        'service_name': SERVICE_NAME,
        'status': status,
        'last_heartbeat': ts,
        'meta': meta
    }]
    ws_write('service_health', rows)


def read_source_file(filepath: str) -> Optional[str]:
    """Read source file content."""
    try:
        if os.path.exists(filepath):
            with open(filepath, 'r') as f:
                return f.read()
        return None
    except Exception as e:
        logger.error(f"Failed to read {filepath}: {e}")
        return None


def parse_signal_analyser_for_supply_chain(source: str) -> Dict[str, Any]:
    """Parse signal_analyser source for supply_chain wiring details."""
    result = {
        'has_supply_chain_logic': False,
        'imports_supply_chain': False,
        'calls_compute_score': False,
        'reads_mcp_signal_enrichments': False,
        'signal_type_check': False,
        'supply_chain_import_name': None,
        'calls_to_compute_score': [],
        'enrichments_table_queries': [],
    }
    
    try:
        tree = ast.parse(source)
        
        for node in ast.walk(tree):
            # Check imports
            if isinstance(node, ast.ImportFrom):
                if node.module and 'supply_chain' in node.module.lower():
                    result['imports_supply_chain'] = True
                    for alias in node.names:
                        result['supply_chain_import_name'] = alias.name
                        logger.info(f"Found supply_chain import: {alias.name} from {node.module}")
            
            # Check for compute_score calls
            if isinstance(node, ast.Call):
                if isinstance(node.func, ast.Attribute):
                    if node.func.attr == 'compute_score':
                        result['calls_compute_score'] = True
                        result['calls_to_compute_score'].append(ast.unparse(node))
                        logger.info(f"Found compute_score call: {ast.unparse(node)}")
            
            # Check for mcp_signal_enrichments table references
            if isinstance(node, ast.Name):
                if 'mcp_signal_enrichments' in node.id or 'enrichments' in node.id.lower():
                    logger.info(f"Found enrichments reference: {node.id}")
        
        # Check for supply_chain logic
        if 'supply_chain' in source.lower():
            result['has_supply_chain_logic'] = True
            logger.info("Source contains 'supply_chain' logic")
        
        # Check for signal_type == 'supply_chain' checks
        if "signal_type" in source and "supply_chain" in source:
            result['signal_type_check'] = True
            logger.info("Found signal_type == 'supply_chain' check")
        
        # Check for mcp_signal_enrichments queries
        if 'mcp_signal_enrichments' in source:
            result['reads_mcp_signal_enrichments'] = True
            logger.info("Source reads from mcp_signal_enrichments table")
            
    except Exception as e:
        logger.error(f"Failed to parse source: {e}")
    
    return result


def check_mcp_signal_enrichments_table() -> Dict[str, Any]:
    """Check mcp_signal_enrichments table for supply_chain entries."""
    result = {
        'table_exists': False,
        'has_supply_chain_rows': False,
        'supply_chain_count': 0,
        'sample_rows': [],
        'columns': [],
    }
    
    # Check if table exists
    check_sql = """
    SELECT column_name, data_type 
    FROM information_schema.columns 
    WHERE table_name = 'mcp_signal_enrichments'
    ORDER BY ordinal_position
    """
    columns = ws_query(check_sql)
    if columns:
        result['table_exists'] = True
        result['columns'] = columns
        logger.info(f"mcp_signal_enrichments table exists with columns: {[c['column_name'] for c in columns]}")
    else:
        logger.warning("mcp_signal_enrichments table not found")
        return result
    
    # Check for supply_chain entries
    supply_chain_sql = """
    SELECT * FROM mcp_signal_enrichments 
    WHERE signal_type = 'supply_chain'
    LIMIT 10
    """
    rows = ws_query(supply_chain_sql)
    if rows:
        result['has_supply_chain_rows'] = True
        result['sample_rows'] = rows
        result['supply_chain_count'] = len(rows)
        logger.info(f"Found {len(rows)} supply_chain rows in mcp_signal_enrichments")
    else:
        logger.info("No supply_chain rows found in mcp_signal_enrichments")
    
    return result


def check_signal_analyser_table() -> Dict[str, Any]:
    """Check mcp_signal_scores table for supply_chain entries from signal_analyser."""
    result = {
        'table_exists': False,
        'has_supply_chain_scores': False,
        'supply_chain_count': 0,
        'recent_scores': [],
    }
    
    # Check if table exists
    check_sql = """
    SELECT column_name FROM information_schema.columns 
    WHERE table_name = 'mcp_signal_scores'
    """
    columns = ws_query(check_sql)
    if columns:
        result['table_exists'] = True
        logger.info("mcp_signal_scores table exists")
    else:
        logger.warning("mcp_signal_scores table not found")
        return result
    
    # Check for recent supply_chain scores
    recent_sql = """
    SELECT * FROM mcp_signal_scores 
    WHERE signal_type = 'supply_chain'
    ORDER BY computed_at DESC
    LIMIT 10
    """
    rows = ws_query(recent_sql)
    if rows:
        result['has_supply_chain_scores'] = True
        result['recent_scores'] = rows
        result['supply_chain_count'] = len(rows)
        logger.info(f"Found {len(rows)} supply_chain scores in mcp_signal_scores")
    else:
        logger.info("No supply_chain scores found in mcp_signal_scores")
    
    return result


def verify_wiring() -> Dict[str, Any]:
    """Verify wiring between signal_analyser and supply_chain_enrichment."""
    logger.info("=" * 60)
    logger.info("WIRING VERIFICATION: signal_analyser -> supply_chain_enrichment")
    logger.info("=" * 60)
    
    findings = {
        'status': 'PASS',
        'issues': [],
        'warnings': [],
        'details': {},
    }
    
    # Step 1: Check signal_analyser source files
    analyser_files = [
        '/home/workspace/zo_sentinel/signal_analyser_v2.py',
        '/home/workspace/zo_sentinel/signal_analyser.py',
    ]
    
    analyser_parsed = None
    found_analyser = None
    
    for filepath in analyser_files:
        source = read_source_file(filepath)
        if source:
            found_analyser = filepath
            analyser_parsed = parse_signal_analyser_for_supply_chain(source)
            logger.info(f"Parsed {filepath}: {analyser_parsed}")
            break
    
    if not found_analyser:
        findings['status'] = 'FAIL'
        findings['issues'].append("signal_analyser.py or signal_analyser_v2.py not found")
        return findings
    
    findings['details']['analyser_file'] = found_analyser
    findings['details']['analyser_parsed'] = analyser_parsed
    
    # Step 2: Check supply_chain_enrichment source
    supply_chain_source = read_source_file('/home/workspace/zo_sentinel/supply_chain_enrichment.py')
    if not supply_chain_source:
        findings['status'] = 'WARN'
        findings['warnings'].append("supply_chain_enrichment.py not found - cannot verify compute_score() exists")
    else:
        if 'compute_score' in supply_chain_source:
            logger.info("supply_chain_enrichment.py contains compute_score() function")
        else:
            findings['status'] = 'FAIL'
            findings['issues'].append("supply_chain_enrichment.py does not contain compute_score() function")
    
    # Step 3: Evaluate wiring quality
    wiring_checks = {
        'has_supply_chain_logic': analyser_parsed.get('has_supply_chain_logic', False),
        'imports_supply_chain_module': analyser_parsed.get('imports_supply_chain', False),
        'calls_compute_score': analyser_parsed.get('calls_compute_score', False),
        'reads_mcp_signal_enrichments': analyser_parsed.get('reads_mcp_signal_enrichments', False),
        'signal_type_check': analyser_parsed.get('signal_type_check', False),
    }
    
    findings['details']['wiring_checks'] = wiring_checks
    
    # Evaluate results
    critical_failures = 0
    
    if not wiring_checks['has_supply_chain_logic']:
        findings['issues'].append("signal_analyser does not have supply_chain logic")
        critical_failures += 1
    
    if not wiring_checks['reads_mcp_signal_enrichments']:
        findings['issues'].append("signal_analyser does not read from mcp_signal_enrichments table")
        critical_failures += 1
    
    if wiring_checks['calls_compute_score']:
        logger.info("signal_analyser calls compute_score() - GOOD")
    else:
        if wiring_checks['imports_supply_chain_module']:
            findings['warnings'].append("signal_analyser imports supply_chain but may not call compute_score()")
    
    # Step 4: Check actual data flow
    enrichments_check = check_mcp_signal_enrichments_table()
    findings['details']['enrichments_table'] = enrichments_check
    
    scores_check = check_signal_analyser_table()
    findings['details']['signal_scores'] = scores_check
    
    # Step 5: Correlation analysis
    if enrichments_check['has_supply_chain_rows'] and not scores_check['has_supply_chain_scores']:
        findings['warnings'].append(
            "supply_chain enrichment data exists in mcp_signal_enrichments but no scores in mcp_signal_scores - "
            "possible disconnect between enrichment and scoring"
        )
    
    if not enrichments_check['has_supply_chain_rows']:
        findings['warnings'].append(
            "No supply_chain rows in mcp_signal_enrichments - enricher may not be running or no data ingested"
        )
    
    # Final status determination
    if critical_failures > 0:
        findings['status'] = 'FAIL'
    elif len(findings['warnings']) > 2:
        findings['status'] = 'WARN'
    
    return findings


def run_diagnostic() -> int:
    """Run the wiring diagnostic."""
    logger.info("Starting supply_chain_enrichment wiring diagnostic")
    
    try:
        findings = verify_wiring()
        
        # Log summary
        logger.info("=" * 60)
        logger.info("DIAGNOSTIC SUMMARY")
        logger.info("=" * 60)
        logger.info(f"Status: {findings['status']}")
        
        if findings['issues']:
            logger.info("ISSUES:")
            for issue in findings['issues']:
                logger.info(f"  - {issue}")
        
        if findings['warnings']:
            logger.info("WARNINGS:")
            for warning in findings['warnings']:
                logger.info(f"  - {warning}")
        
        # Log wiring checks
        if 'wiring_checks' in findings['details']:
            logger.info("WIRING CHECKS:")
            for check, passed in findings['details']['wiring_checks'].items():
                status = "PASS" if passed else "FAIL"
                logger.info(f"  [{status}] {check}: {passed}")
        
        # Log table status
        if 'enrichments_table' in findings['details']:
            et = findings['details']['enrichments_table']
            logger.info(f"mcp_signal_enrichments: exists={et['table_exists']}, supply_chain_rows={et['supply_chain_count']}")
        
        if 'signal_scores' in findings['details']:
            ss = findings['details']['signal_scores']
            logger.info(f"mcp_signal_scores: exists={ss['table_exists']}, supply_chain_scores={ss['supply_chain_count']}")
        
        # Write results to audit table
        ts = datetime.now(timezone.utc).isoformat()
        result_row = {
            'diagnostic_name': 'supply_chain_enrichment_wiring',
            'run_at': ts,
            'status': findings['status'],
            'analyser_file': findings['details'].get('analyser_file', 'NOT_FOUND'),
            'wiring_checks': str(findings['details'].get('wiring_checks', {})),
            'enrichments_count': findings['details'].get('enrichments_table', {}).get('supply_chain_count', 0),
            'scores_count': findings['details'].get('signal_scores', {}).get('supply_chain_count', 0),
            'issues': '; '.join(findings['issues']) if findings['issues'] else None,
            'warnings': '; '.join(findings['warnings']) if findings['warnings'] else None,
        }
        
        ws_write('diagnostic_results', [result_row])
        
        # Send heartbeat
        send_heartbeat('healthy', {
            'status': findings['status'],
            'issues_count': len(findings['issues']),
            'warnings_count': len(findings['warnings']),
        })
        
        logger.info("Diagnostic complete")
        
        # Exit code: 0 for success, 1 for issues found
        if findings['status'] == 'FAIL':
            return 1
        return 0
        
    except Exception as e:
        logger.error(f"Diagnostic failed with exception: {e}")
        send_heartbeat('error', {'error': str(e)})
        return 1


if __name__ == '__main__':
    sys.exit(run_diagnostic())