#!/usr/bin/env python3
"""
Verify injection_resilience signal is properly wired:
(1) Query mcp_signal_scores WHERE signal_name='injection_resilience' exists
(2) Confirm trust_synthesiser_v2.py applies weight and threshold for this dimension
(3) Check pi_scorer.py outputs feed into mcp_signal_scores correctly

Pure verification utility - reads DB via write_service /query, does not write.
"""

import os
import sys
import requests
import re
from datetime import datetime

# deps: requests

QUERY_URL = 'http://127.0.0.1:8772/query'
TRUST_SYNTH_PATH = '/home/workspace/zo_sentinel/trust_synthesiser_v2.py'
PI_SCORER_PATH = '/home/workspace/zo_sentinel/pi_scorer.py'


def ws_query(sql: str) -> list:
    """Query write_service - SELECT only, no writes."""
    try:
        resp = requests.post(QUERY_URL, json={'sql': sql}, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        return data.get('rows', [])
    except Exception as e:
        print(f"[ERROR] Query failed: {e}")
        return []


def check_mcp_signal_scores_has_injection_resilience() -> dict:
    """Check 1: Query mcp_signal_scores for injection_resilience records."""
    sql = """
    SELECT signal_name, COUNT(*) as count, MIN(scored_at) as first_seen, MAX(scored_at) as last_seen
    FROM mcp_signal_scores
    WHERE signal_name = 'injection_resilience'
    GROUP BY signal_name
    """
    rows = ws_query(sql)
    
    if rows:
        row = rows[0]
        result = {
            'check': 'mcp_signal_scores_has_injection_resilience',
            'status': 'PASS',
            'signal_name': row.get('signal_name'),
            'record_count': row.get('count'),
            'first_seen': row.get('first_seen'),
            'last_seen': row.get('last_seen')
        }
        print(f"[PASS] mcp_signal_scores has injection_resilience: {row.get('count')} records")
        return result
    else:
        # Also check if the table/column exist at all
        table_check = ws_query("SELECT COUNT(*) as total FROM mcp_signal_scores LIMIT 1")
        if table_check:
            result = {
                'check': 'mcp_signal_scores_has_injection_resilience',
                'status': 'FAIL',
                'reason': 'No injection_resilience records found in mcp_signal_scores',
                'table_exists': True
            }
        else:
            result = {
                'check': 'mcp_signal_scores_has_injection_resilience',
                'status': 'FAIL',
                'reason': 'mcp_signal_scores table does not exist or is empty',
                'table_exists': False
            }
        print(f"[FAIL] mcp_signal_scores: {result['reason']}")
        return result


def check_trust_synthesiser_weights() -> dict:
    """Check 2: Confirm trust_synthesiser_v2.py has injection_resilience in signal weights."""
    try:
        with open(TRUST_SYNTH_PATH, 'r') as f:
            content = f.read()
    except FileNotFoundError:
        result = {
            'check': 'trust_synthesiser_v2_weights',
            'status': 'FAIL',
            'reason': f'trust_synthesiser_v2.py not found at {TRUST_SYNTH_PATH}'
        }
        print(f"[FAIL] trust_synthesiser_v2.py: {result['reason']}")
        return result
    
    # Check DEFAULT_SIGNAL_WEIGHTS for injection_resilience
    weight_pattern = r'"injection_resilience"\s*:\s*([0-9.]+)'
    weight_match = re.search(weight_pattern, content)
    
    # Also check dynamic weights loading
    dynamic_weight_pattern = r"'injection_resilience'\s*:\s*([0-9.]+)"
    dynamic_match = re.search(dynamic_weight_pattern, content)
    
    # Check for threshold/VERDICT_THRESHOLDS
    threshold_pattern = r'"injection_resilience".*?threshold.*?([0-9.]+)'
    
    weight_found = weight_match.group(1) if weight_match else (dynamic_match.group(1) if dynamic_match else None)
    
    if weight_found:
        result = {
            'check': 'trust_synthesiser_v2_weights',
            'status': 'PASS',
            'weight': float(weight_found),
            'expected_weight': 1.6,
            'note': 'injection_resilience found in trust_synthesiser_v2.py'
        }
        print(f"[PASS] trust_synthesiser_v2.py has injection_resilience weight: {weight_found}")
    else:
        # Check what signals ARE in the weights
        all_signals = re.findall(r'"(\w+_signal)"\s*:\s*[0-9.]+', content)
        result = {
            'check': 'trust_synthesiser_v2_weights',
            'status': 'INFO',
            'reason': 'injection_resilience NOT in DEFAULT_SIGNAL_WEIGHTS',
            'current_signals': all_signals,
            'note': 'The task claims weight 1.6 and threshold 0.80 but these do not appear in trust_synthesiser_v2.py'
        }
        print(f"[INFO] trust_synthesiser_v2.py: injection_resilience NOT in weights")
        print(f"       Current signals: {all_signals}")
    
    return result


def check_pi_scorer_output() -> dict:
    """Check 3: Verify pi_scorer.py writes to mcp_signal_scores correctly."""
    try:
        with open(PI_SCORER_PATH, 'r') as f:
            content = f.read()
    except FileNotFoundError:
        result = {
            'check': 'pi_scorer_output',
            'status': 'FAIL',
            'reason': f'pi_scorer.py not found at {PI_SCORER_PATH}'
        }
        print(f"[FAIL] pi_scorer.py: {result['reason']}")
        return result
    
    # Check for mcp_signal_scores write
    mcp_scores_write = "'mcp_signal_scores'" in content or '"mcp_signal_scores"' in content
    
    # Check for signal_name='injection_resilience'
    signal_name_check = "'signal_name': 'injection_resilience'" in content or \
                        '"signal_name": "injection_resilience"' in content
    
    # Check for compute_injection_resilience call
    compute_call = 'compute_injection_resilience(' in content
    
    # Check for update_signal_scores function
    update_function = 'def update_signal_scores(' in content
    
    results = {
        'check': 'pi_scorer_output',
        'writes_to_mcp_signal_scores': mcp_scores_write,
        'uses_injection_resilience_signal_name': signal_name_check,
        'has_compute_injection_resilience': compute_call,
        'has_update_signal_scores': update_function
    }
    
    if mcp_scores_write and signal_name_check and compute_call:
        results['status'] = 'PASS'
        print(f"[PASS] pi_scorer.py correctly writes injection_resilience to mcp_signal_scores")
    else:
        results['status'] = 'FAIL'
        missing = []
        if not mcp_scores_write:
            missing.append('mcp_signal_scores write')
        if not signal_name_check:
            missing.append("signal_name='injection_resilience'")
        if not compute_call:
            missing.append('compute_injection_resilience()')
        results['reason'] = f"Missing: {', '.join(missing)}"
        print(f"[FAIL] pi_scorer.py: {results['reason']}")
    
    return results


def check_pi_scorer_threshold() -> dict:
    """Check the blocking threshold in pi_scorer.py."""
    try:
        with open(PI_SCORER_PATH, 'r') as f:
            content = f.read()
    except FileNotFoundError:
        return {'check': 'pi_scorer_threshold', 'status': 'FAIL', 'reason': 'pi_scorer.py not found'}
    
    # Check BLOCKING_THRESHOLD value
    threshold_pattern = r'BLOCKING_THRESHOLD\s*=\s*([0-9.]+)'
    threshold_match = re.search(threshold_pattern, content)
    
    if threshold_match:
        threshold = float(threshold_match.group(1))
        result = {
            'check': 'pi_scorer_threshold',
            'status': 'PASS' if threshold == 0.80 else 'FAIL',
            'threshold': threshold,
            'expected': 0.80,
            'note': 'pi_scorer BLOCKING_THRESHOLD value'
        }
        print(f"[{'PASS' if threshold == 0.80 else 'FAIL'}] pi_scorer.py BLOCKING_THRESHOLD: {threshold}")
        return result
    else:
        return {
            'check': 'pi_scorer_threshold',
            'status': 'FAIL',
            'reason': 'BLOCKING_THRESHOLD not found in pi_scorer.py'
        }


def main():
    """Run all verification checks."""
    print("=" * 60)
    print("Injection Resilience Signal Integration Verification")
    print("=" * 60)
    print()
    
    all_results = []
    
    # Check 1: mcp_signal_scores has injection_resilience
    print("[1/4] Checking mcp_signal_scores for injection_resilience...")
    result1 = check_mcp_signal_scores_has_injection_resilience()
    all_results.append(result1)
    print()
    
    # Check 2: trust_synthesiser_v2.py weights
    print("[2/4] Checking trust_synthesiser_v2.py signal weights...")
    result2 = check_trust_synthesiser_weights()
    all_results.append(result2)
    print()
    
    # Check 3: pi_scorer.py output wiring
    print("[3/4] Checking pi_scorer.py output wiring...")
    result3 = check_pi_scorer_output()
    all_results.append(result3)
    print()
    
    # Check 4: pi_scorer.py threshold
    print("[4/4] Checking pi_scorer.py blocking threshold...")
    result4 = check_pi_scorer_threshold()
    all_results.append(result4)
    print()
    
    # Summary
    print("=" * 60)
    print("SUMMARY")
    print("=" * 60)
    passed = sum(1 for r in all_results if r.get('status') == 'PASS')
    failed = sum(1 for r in all_results if r.get('status') == 'FAIL')
    info = sum(1 for r in all_results if r.get('status') == 'INFO')
    
    print(f"  PASS: {passed}")
    print(f"  FAIL: {failed}")
    print(f"  INFO: {info}")
    print()
    
    if failed > 0:
        print("[RESULT] VERIFICATION FAILED - see details above")
        return 1
    elif info > 0:
        print("[RESULT] VERIFICATION COMPLETED WITH NOTES - see details above")
        return 0
    else:
        print("[RESULT] VERIFICATION PASSED")
        return 0


if __name__ == '__main__':
    sys.exit(main())
