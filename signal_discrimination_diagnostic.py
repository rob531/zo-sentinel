import logging
import os
import sys
import requests
from datetime import datetime, timezone
import hashlib
import json

WRITE_SERVICE_URL = 'http://localhost:8772'
SERVICE_NAME = 'signal_discrimination_diagnostic'

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(name)s] %(levelname)s: %(message)s',
    handlers=[
        logging.FileHandler(f'/home/workspace/logs/{SERVICE_NAME}.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

SIGNAL_TYPES = ['permission_scope', 'temporal_stability', 'tool_description_safety']
SIGNAL_ANALYSER_PATH = '/home/workspace/zo_sentinel/signal_analyser.py'
ENRICHMENT_PATHS = [
    '/home/workspace/zo_sentinel/permission_scope_enrichment.py',
    '/home/workspace/zo_sentinel/temporal_stability_enrichment.py',
    '/home/workspace/zo_sentinel/tool_description_safety_enrichment.py'
]

def ws_query(sql, params=None):
    payload = {'table': '__query__', 'sql': sql}
    if params:
        payload['params'] = params
    resp = requests.post(WRITE_SERVICE_URL, json=payload, timeout=30)
    resp.raise_for_status()
    return resp.json().get('rows', [])

def ws_write(table, rows):
    if not rows:
        return
    payload = {'table': table, 'rows': rows, 'wait': True}
    resp = requests.post(WRITE_SERVICE_URL, json=payload, timeout=30)
    resp.raise_for_status()
    return resp.json()

def send_heartbeat(status='running', meta=None):
    row = {
        'service_name': SERVICE_NAME,
        'status': status,
        'last_heartbeat': datetime.now(timezone.utc).isoformat(),
        'meta': json.dumps(meta) if meta else '{}'
    }
    ws_write('service_health', [row])

def diagnose_signal_cardinality():
    logger.info("Diagnosing signal cardinality plateau across signal types")
    results = {}
    
    for signal_type in SIGNAL_TYPES:
        try:
            sql = """
            SELECT 
                signal_type,
                COUNT(DISTINCT signal_value) as distinct_values,
                COUNT(*) as total_records,
                signal_value as sample_values,
                MIN(computed_at) as earliest_computation,
                MAX(computed_at) as latest_computation
            FROM mcp_signal_scores 
            WHERE signal_type = ?
            GROUP BY signal_type, signal_value
            ORDER BY signal_type, signal_value
            """
            rows = ws_query(sql, [signal_type])
            results[signal_type] = {
                'query_success': True,
                'data': rows
            }
            logger.info(f"{signal_type}: Found {len(rows)} distinct value groups")
        except Exception as e:
            logger.error(f"Failed to query {signal_type}: {e}")
            results[signal_type] = {'query_success': False, 'error': str(e)}
    
    return results

def read_source_files():
    source_analysis = {}
    
    if os.path.exists(SIGNAL_ANALYSER_PATH):
        with open(SIGNAL_ANALYSER_PATH, 'r') as f:
            source_analysis['signal_analyser'] = f.read()
        logger.info(f"Read signal_analyser.py ({len(source_analysis['signal_analyser'])} bytes)")
    else:
        logger.warning(f"signal_analyser.py not found at {SIGNAL_ANALYSER_PATH}")
        source_analysis['signal_analyser'] = None
    
    for enrichment_path in ENRICHMENT_PATHS:
        filename = os.path.basename(enrichment_path)
        if os.path.exists(enrichment_path):
            with open(enrichment_path, 'r') as f:
                source_analysis[filename] = f.read()
            logger.info(f"Read {filename} ({len(source_analysis[filename])} bytes)")
        else:
            logger.warning(f"{filename} not found")
            source_analysis[filename] = None
    
    return source_analysis

def extract_binning_logic(source_analysis):
    findings = {}
    
    for signal_type in SIGNAL_TYPES:
        filename = f"{signal_type}_enrichment.py"
        if filename not in source_analysis or not source_analysis[filename]:
            findings[signal_type] = {'source_found': False}
            continue
        
        source = source_analysis[filename]
        lines = source.split('\n')
        
        bin_thresholds = []
        value_mappings = []
        quantile_specs = []
        
        for i, line in enumerate(lines):
            stripped = line.strip()
            if 'threshold' in stripped.lower() or 'cutoff' in stripped.lower() or 'boundary' in stripped.lower():
                bin_thresholds.append({'line': i+1, 'content': stripped})
            if 'quantile' in stripped.lower() or 'percentile' in stripped.lower():
                quantile_specs.append({'line': i+1, 'content': stripped})
            if 'value_mapping' in stripped.lower() or 'score_map' in stripped.lower() or 'enum' in stripped.lower():
                value_mappings.append({'line': i+1, 'content': stripped})
            if stripped.startswith('=') or stripped.startswith('return'):
                if any(char.isdigit() for char in stripped):
                    value_mappings.append({'line': i+1, 'content': stripped})
        
        findings[signal_type] = {
            'source_found': True,
            'bin_thresholds': bin_thresholds,
            'value_mappings': value_mappings,
            'quantile_specs': quantile_specs,
            'total_lines': len(lines)
        }
        
        logger.info(f"{signal_type}: {len(bin_thresholds)} thresholds, {len(value_mappings)} mappings found")
    
    return findings

def extract_score_mapping(source):
    score_mappings = {}
    lines = source.split('\n')
    
    in_score_block = False
    block_lines = []
    
    for line in lines:
        if 'score' in line.lower() and ('dict' in line.lower() or '{' in line or 'mapping' in line.lower()):
            in_score_block = True
        if in_score_block:
            block_lines.append(line)
            if '}' in line and in_score_block:
                in_score_block = False
                block_text = '\n'.join(block_lines)
                if '{' in block_text and '}' in block_text:
                    score_mappings['block'] = block_text
    
    return score_mappings

def diagnose_plateau_cause(findings, cardinality_results):
    diagnosis = {}
    
    for signal_type in SIGNAL_TYPES:
        finding = findings.get(signal_type, {})
        cardinality = cardinality_results.get(signal_type, {})
        
        if not finding.get('source_found'):
            diagnosis[signal_type] = {'cause': 'SOURCE_FILE_NOT_FOUND', 'confidence': 'high'}
            continue
        
        distinct_count = len(cardinality.get('data', [])) if cardinality.get('query_success') else 0
        bin_count = len(finding.get('bin_thresholds', []))
        mapping_count = len(finding.get('value_mappings', []))
        
        if bin_count >= 3:
            cause = f"Hard-coded binning with {bin_count} thresholds produces exactly {bin_count + 1} output buckets (expected ~4)"
            confidence = 'high'
        elif mapping_count > 0 and mapping_count <= 4:
            cause = f"Enum-style mapping with {mapping_count} fixed values constrains output to {mapping_count} distinct values"
            confidence = 'medium'
        else:
            cause = "Signal mapping logic appears to cap output at 4 distinct values through quantization or truncation"
            confidence = 'low'
        
        diagnosis[signal_type] = {
            'cause': cause,
            'confidence': confidence,
            'distinct_values_observed': distinct_count,
            'bin_thresholds_found': bin_count,
            'mappings_found': mapping_count,
            'evidence': {
                'thresholds': finding.get('bin_thresholds', [])[:3],
                'mappings': finding.get('value_mappings', [])[:5]
            }
        }
        
        logger.info(f"{signal_type} diagnosis: {cause}")
    
    return diagnosis

def generate_recommendations(diagnosis):
    recommendations = []
    
    for signal_type, diag in diagnosis.items():
        if diag.get('confidence') == 'high':
            recommendations.append({
                'signal_type': signal_type,
                'issue': 'Signal plateaus at 4 values due to hard-coded binning/mapping',
                'recommendation': f'Increase granularity in {signal_type}_enrichment.py: replace fixed thresholds with continuous scoring or quantile-based bucketing',
                'priority': 'high'
            })
        elif diag.get('confidence') == 'medium':
            recommendations.append({
                'signal_type': signal_type,
                'issue': 'Signal may be constrained by enum mapping',
                'recommendation': f'Replace categorical mapping with continuous float scoring in {signal_type}_enrichment.py',
                'priority': 'medium'
            })
    
    return recommendations

def store_diagnostic_results(cardinality_results, findings, diagnosis, recommendations):
    diagnostic_record = {
        'diagnostic_id': hashlib.md5(f"{SERVICE_NAME}_{datetime.now(timezone.utc).isoformat()}".encode()).hexdigest()[:16],
        'service_name': SERVICE_NAME,
        'run_at': datetime.now(timezone.utc).isoformat(),
        'cardinality_summary': json.dumps(cardinality_results),
        'findings_summary': json.dumps({k: {kk: vv[:3] if isinstance(vv, list) else vv 
                                             for kk, vv in v.items() if kk != 'evidence'}
                                        for k, v in findings.items()}),
        'diagnosis': json.dumps(diagnosis),
        'recommendations': json.dumps(recommendations),
        'signal_types_analyzed': ','.join(SIGNAL_TYPES)
    }
    
    try:
        ws_write('signal_diagnostic_results', [diagnostic_record])
        logger.info("Stored diagnostic results to signal_diagnostic_results table")
    except Exception as e:
        logger.error(f"Failed to write diagnostic results: {e}")

def main():
    logger.info(f"Starting {SERVICE_NAME} diagnostic run")
    
    send_heartbeat(status='running', meta={'phase': 'cardinality_query'})
    cardinality_results = diagnose_signal_cardinality()
    
    send_heartbeat(status='running', meta={'phase': 'source_analysis'})
    source_analysis = read_source_files()
    
    send_heartbeat(status='running', meta={'phase': 'binning_analysis'})
    findings = extract_binning_logic(source_analysis)
    
    send_heartbeat(status='running', meta={'phase': 'diagnosis'})
    diagnosis = diagnose_plateau_cause(findings, cardinality_results)
    
    send_heartbeat(status='running', meta={'phase': 'recommendations'})
    recommendations = generate_recommendations(diagnosis)
    
    logger.info("=" * 60)
    logger.info("SIGNAL DISCRIMINATION DIAGNOSTIC REPORT")
    logger.info("=" * 60)
    logger.info(f"Analyzed signal types: {SIGNAL_TYPES}")
    
    for signal_type in SIGNAL_TYPES:
        diag = diagnosis.get(signal_type, {})
        logger.info(f"\n{signal_type.upper()}:")
        logger.info(f"  Cause: {diag.get('cause', 'Unknown')}")
        logger.info(f"  Confidence: {diag.get('confidence', 'Unknown')}")
        logger.info(f"  Distinct values observed: {diag.get('distinct_values_observed', 'N/A')}")
        logger.info(f"  Bin thresholds found: {diag.get('bin_thresholds_found', 0)}")
    
    logger.info("\nRECOMMENDATIONS:")
    for rec in recommendations:
        logger.info(f"  [{rec['priority'].upper()}] {rec['signal_type']}: {rec['recommendation']}")
    
    logger.info("=" * 60)
    
    send_heartbeat(status='running', meta={'phase': 'persisting'})
    store_diagnostic_results(cardinality_results, findings, diagnosis, recommendations)
    
    send_heartbeat(status='completed', meta={'recommendations_count': len(recommendations)})
    logger.info(f"Diagnostic run complete. Generated {len(recommendations)} recommendations.")
    
    sys.exit(0)

if __name__ == '__main__':
    main()