#!/usr/bin/env python3
"""
ZO-SENTINEL: trust_synthesiser_v2 Quality Audit
Checks if trust_synthesiser_v2.py implements injection_resilience weighting correctly.
"""

import sys
sys.path.insert(0, '/home/workspace')

import requests
import json
from datetime import datetime

# Read the source file
SOURCE_FILE = '/home/workspace/services/trust_synthesiser_v2.py'

def read_source():
    try:
        with open(SOURCE_FILE, 'r') as f:
            return f.read()
    except FileNotFoundError:
        return None

def query_mcp_signal_scores_schema():
    """Query the mcp_signal_scores table schema via write_service."""
    try:
        response = requests.post(
            'http://127.0.0.1:8772/query',
            json={"sql": "SELECT * FROM mcp_signal_scores LIMIT 1"},
            timeout=10
        )
        if response.status_code == 200:
            return response.json()
    except Exception as e:
        print(f"Schema query error: {e}")
    return None

def query_injection_resilience_signals():
    """Query existing injection_resilience signals in mcp_signal_scores."""
    try:
        response = requests.post(
            'http://127.0.0.1:8772/query',
            json={"sql": "SELECT * FROM mcp_signal_scores WHERE signal_name LIKE '%injection%' OR signal_name LIKE '%resilience%' LIMIT 10"},
            timeout=10
        )
        if response.status_code == 200:
            return response.json()
    except Exception as e:
        print(f"Signal query error: {e}")
    return None

def check_injection_resilience_implementation(source_code):
    """Check if source code implements injection_resilience weighting."""
    findings = {
        'has_injection_resilience_query': False,
        'has_weight_1_6': False,
        'has_threshold_0_80': False,
        'has_dimension_filter': False,
        'matching_patterns': []
    }
    
    if not source_code:
        return findings
    
    # Check for dimension filter pattern
    dimension_patterns = [
        "dimension = 'injection_resilience'",
        "dimension='injection_resilience'",
        "signal_name = 'injection_resilience'",
        "signal_name='injection_resilience'"
    ]
    
    for pattern in dimension_patterns:
        if pattern in source_code:
            findings['has_injection_resilience_query'] = True
            findings['matching_patterns'].append(f"Found dimension filter: {pattern}")
    
    # Check for weight 1.6
    weight_patterns = ['1.6', '1.60', 'weight * 1.6', '* 1.6', '1.6 *', 'weight=1.6', 'weight: 1.6']
    for pattern in weight_patterns:
        if pattern in source_code:
            findings['has_weight_1_6'] = True
            findings['matching_patterns'].append(f"Found weight pattern: {pattern}")
    
    # Check for threshold 0.80
    threshold_patterns = ['0.80', '0.8', 'threshold=0.80', 'threshold: 0.80', 'threshold > 0.80', 'threshold < 0.80']
    for pattern in threshold_patterns:
        if pattern in source_code:
            findings['has_threshold_0_80'] = True
            findings['matching_patterns'].append(f"Found threshold pattern: {pattern}")
    
    return findings

def generate_audit_report():
    """Generate comprehensive audit report."""
    source_code = read_source()
    schema_data = query_mcp_signal_scores_schema()
    injection_signals = query_injection_resilience_signals()
    findings = check_injection_resilience_implementation(source_code)
    
    report = []
    report.append("=" * 70)
    report.append("ZO-SENTINEL: trust_synthesiser_v2 Quality Audit Report")
    report.append(f"Timestamp: {datetime.now().isoformat()}")
    report.append("=" * 70)
    
    # Source file status
    report.append("\n[1] SOURCE FILE STATUS")
    if source_code:
        report.append(f"    ✓ File found: {SOURCE_FILE}")
        report.append(f"    ✓ Source length: {len(source_code)} characters")
    else:
        report.append(f"    ✗ File NOT found: {SOURCE_FILE}")
        report.append("    → CANNOT COMPLETE AUDIT")
        return "\n".join(report)
    
    # Schema information
    report.append("\n[2] mcp_signal_scores SCHEMA")
    if schema_data and schema_data.get('rows'):
        report.append("    ✓ Table accessible via write_service")
        report.append("    ✓ Sample row structure:")
        for col in schema_data['rows'][0].keys():
            report.append(f"       - {col}")
    else:
        report.append("    ? Table query returned no data (may be empty)")
    
    # Injection resilience signals
    report.append("\n[3] INJECTION_RESILIENCE SIGNALS IN DATABASE")
    if injection_signals and injection_signals.get('rows'):
        report.append(f"    ✓ Found {len(injection_signals['rows'])} matching signals")
        for row in injection_signals['rows']:
            report.append(f"       - server_id: {row.get('server_id')}, signal: {row.get('signal_name')}, score: {row.get('score')}")
    else:
        report.append("    ? No injection_resilience signals found in database")
    
    # Implementation check
    report.append("\n[4] INJECTION_RESILIENCE IMPLEMENTATION CHECK")
    report.append(f"    Dimension filter (WHERE dimension='injection_resilience'): {'✓ PASS' if findings['has_injection_resilience_query'] else '✗ FAIL'}")
    report.append(f"    Weight 1.6 applied: {'✓ PASS' if findings['has_weight_1_6'] else '✗ FAIL'}")
    report.append(f"    Threshold 0.80 configured: {'✓ PASS' if findings['has_threshold_0_80'] else '✗ FAIL'}")
    
    if findings['matching_patterns']:
        report.append("\n    Matching patterns found:")
        for pattern in findings['matching_patterns']:
            report.append(f"       • {pattern}")
    
    # Overall verdict
    all_checks_pass = (
        findings['has_injection_resilience_query'] and
        findings['has_weight_1_6'] and
        findings['has_threshold_0_80']
    )
    
    report.append("\n[5] OVERALL VERDICT")
    if all_checks_pass:
        report.append("    ✓ INJECTION_RESILIENCE WEIGHTING CORRECTLY IMPLEMENTED")
        report.append("    → No companion module required")
    else:
        report.append("    ✗ INJECTION_RESILIENCE WEIGHTING MISSING OR INCOMPLETE")
        report.append("    → Companion module recommended: trust_synthesiser_v2_pi_dimension.py")
        report.append("\n[6] RECOMMENDED COMPANION MODULE")
        report.append("    File: trust_synthesiser_v2_pi_dimension.py")
        report.append("    Purpose: Add injection_resilience dimension weighting")
        report.append("    Requirements:")
        report.append("       - Query: SELECT score FROM mcp_signal_scores WHERE dimension='injection_resilience'")
        report.append("       - Apply weight: 1.6")
        report.append("       - Threshold: 0.80 (pass if score >= 0.80 after weighting)")
        report.append("       - Integrate with main trust synthesis flow")
    
    report.append("\n" + "=" * 70)
    return "\n".join(report)

def create_companion_module():
    """Create companion module content if injection_resilience is missing."""
    source_code = read_source()
    findings = check_injection_resilience_implementation(source_code)
    
    all_checks_pass = (
        findings['has_injection_resilience_query'] and
        findings['has_weight_1_6'] and
        findings['has_threshold_0_80']
    )
    
    if all_checks_pass:
        return None  # No companion needed
    
    # Generate companion module
    companion_code = '''#!/usr/bin/env python3
"""
ZO-SENTINEL: trust_synthesiser_v2_pi_dimension.py
Companion module for injection_resilience dimension weighting.

CRITICAL REQUIREMENTS (from quality audit):
- Read mcp_signal_scores WHERE dimension='injection_resilience'
- Apply weight: 1.6
- Threshold: 0.80 (score must meet this after weighting)

INTEGRATION: Import and call apply_injection_resilience_score() from trust_synthesiser_v2.py
"""

import sys
sys.path.insert(0, '/home/workspace')

import requests
from typing import Dict, Optional

WRITE_SERVICE_URL = 'http://127.0.0.1:8772/write'
QUERY_SERVICE_URL = 'http://127.0.0.1:8772/query'

# Quality audit constants
INJECTION_RESILIENCE_WEIGHT = 1.6
INJECTION_RESILIENCE_THRESHOLD = 0.80


def query_injection_resilience_signals(server_id: Optional[str] = None) -> list:
    """
    Query injection_resilience signals from mcp_signal_scores.
    
    Args:
        server_id: Optional server_id filter
        
    Returns:
        List of signal rows with injection_resilience dimension
    """
    sql = "SELECT server_id, signal_name, score, evidence, scored_at FROM mcp_signal_scores WHERE signal_name = 'injection_resilience'"
    if server_id:
        sql += f" AND server_id = '{server_id}'"
    
    try:
        response = requests.post(QUERY_SERVICE_URL, json={"sql": sql}, timeout=10)
        if response.status_code == 200:
            data = response.json()
            return data.get('rows', [])
    except Exception as e:
        print(f"Query error: {e}")
    return []


def calculate_injection_resilience_score(server_id: str) -> Dict:
    """
    Calculate injection_resilience score with weight 1.6 and threshold 0.80.
    
    Args:
        server_id: The server to evaluate
        
    Returns:
        Dict with score, weighted_score, passes_threshold, and evidence
    """
    signals = query_injection_resilience_signals(server_id)
    
    if not signals:
        return {
            'server_id': server_id,
            'score': 0.0,
            'weighted_score': 0.0,
            'passes_threshold': False,
            'evidence': 'No injection_resilience signals found'
        }
    
    # Aggregate scores (average if multiple signals)
    raw_score = sum(s.get('score', 0) for s in signals) / len(signals)
    
    # Apply weight 1.6
    weighted_score = raw_score * INJECTION_RESILIENCE_WEIGHT
    
    # Check against threshold 0.80
    passes_threshold = weighted_score >= INJECTION_RESILIENCE_THRESHOLD
    
    # Collect evidence
    evidence_parts = [s.get('evidence', '') for s in signals if s.get('evidence')]
    evidence = '; '.join(evidence_parts) if evidence_parts else f"Based on {len(signals)} injection_resilience signal(s)"
    
    return {
        'server_id': server_id,
        'score': raw_score,
        'weighted_score': weighted_score,
        'passes_threshold': passes_threshold,
        'evidence': evidence,
        'dimension': 'injection_resilience',
        'weight': INJECTION_RESILIENCE_WEIGHT,
        'threshold': INJECTION_RESILIENCE_THRESHOLD
    }


def apply_injection_resilience_score(trust_scores: Dict, server_id: str) -> Dict:
    """
    Apply injection_resilience dimension score to trust scores.
    
    This function should be called during trust synthesis in trust_synthesiser_v2.py
    to incorporate injection_resilience weighting into the final trust score.
    
    Args:
        trust_scores: Existing trust score dictionary (modified in place)
        server_id: Server being evaluated
        
    Returns:
        Updated trust_scores dict with injection_resilience contribution
    """
    ir_result = calculate_injection_resilience_score(server_id)
    
    trust_scores['injection_resilience'] = {
        'raw_score': ir_result['score'],
        'weighted_score': ir_result['weighted_score'],
        'contribution': ir_result['weighted_score'] if ir_result['passes_threshold'] else 0,
        'passes_threshold': ir_result['passes_threshold'],
        'evidence': ir_result['evidence']
    }
    
    return trust_scores


if __name__ == '__main__':
    # Test with sample server_id if provided
    import sys
    if len(sys.argv) > 1:
        server_id = sys.argv[1]
        result = calculate_injection_resilience_score(server_id)
        print(f"\\nInjection Resilience Score for {server_id}:")
        print(f"  Raw Score: {result['score']:.3f}")
        print(f"  Weighted Score (×1.6): {result['weighted_score']:.3f}")
        print(f"  Threshold (0.80): {'PASS' if result['passes_threshold'] else 'FAIL'}")
        print(f"  Evidence: {result['evidence']}")
    else:
        print("Usage: python trust_synthesiser_v2_pi_dimension.py <server_id>")
'''
    
    return companion_code


if __name__ == '__main__':
    print(generate_audit_report())
    
    # If companion module is needed, generate it
    companion_code = create_companion_module()
    if companion_code:
        output_file = '/home/workspace/services/trust_synthesiser_v2_pi_dimension.py'
        try:
            with open(output_file, 'w') as f:
                f.write(companion_code)
            print(f"\nCompanion module written to: {output_file}")
        except Exception as e:
            print(f"\nFailed to write companion module: {e}")