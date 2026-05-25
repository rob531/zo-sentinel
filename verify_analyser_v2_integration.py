import sys
import os
import requests
import json
from datetime import datetime, timezone
from typing import List, Dict, Any, Tuple

sys.path.insert(0, '/home/workspace/zo_sentinel')

WRITE_SERVICE_URL = 'http://localhost:8772'
SERVICE_NAME = 'verify_analyser_v2_integration'

LOG_FILE = '/home/workspace/logs/verify_analyser_v2_integration.log'


def ws_query(sql: str) -> Dict[str, Any]:
    resp = requests.post(
        f'{WRITE_SERVICE_URL}/query',
        json={'sql': sql},
        timeout=30
    )
    resp.raise_for_status()
    return resp.json()


def validate_evidence_blob(row: dict) -> Tuple[bool, str]:
    server_id = row.get('server_id', 'N/A')
    signal_name = row.get('signal_name', 'N/A')
    evidence = row.get('evidence', '')
    
    if not evidence:
        return False, f"server_id={server_id} signal_name={signal_name}: evidence is empty"
    
    try:
        if isinstance(evidence, str):
            blob = json.loads(evidence)
        elif isinstance(evidence, dict):
            blob = evidence
        else:
            return False, f"server_id={server_id} signal_name={signal_name}: evidence is not JSON-serializable"
        
        if not isinstance(blob, dict):
            return False, f"server_id={server_id} signal_name={signal_name}: evidence_blob is not a dict"
        
        if 'signal_type' not in blob:
            return False, f"server_id={server_id} signal_name={signal_name}: missing 'signal_type' in evidence_blob"
        
        signal_type = blob.get('signal_type', '')
        if not isinstance(signal_type, str) or not signal_type.replace('_', '').isalnum():
            return False, f"server_id={server_id} signal_name={signal_name}: signal_type '{signal_type}' not snake_case"
        
        if 'confidence' not in blob:
            return False, f"server_id={server_id} signal_name={signal_name}: missing 'confidence' in evidence_blob"
        
        confidence = blob.get('confidence', -1)
        if not isinstance(confidence, (int, float)) or confidence < 0.0 or confidence > 1.0:
            return False, f"server_id={server_id} signal_name={signal_name}: confidence {confidence} out of range [0.0, 1.0]"
        
        if 'evidence_blob' not in blob:
            return False, f"server_id={server_id} signal_name={signal_name}: missing 'evidence_blob' key"
        
        if not isinstance(blob.get('evidence_blob'), dict):
            return False, f"server_id={server_id} signal_name={signal_name}: evidence_blob.evidence_blob is not a dict"
        
        return True, f"server_id={server_id} signal_name={signal_name}: valid"
        
    except json.JSONDecodeError as e:
        return False, f"server_id={server_id} signal_name={signal_name}: JSON parse error: {e}"
    except Exception as e:
        return False, f"server_id={server_id} signal_name={signal_name}: unexpected error: {e}"


def get_all_signal_types() -> List[str]:
    result = ws_query("""
        SELECT DISTINCT signal_name 
        FROM mcp_signal_scores 
        ORDER BY signal_name
    """)
    rows = result.get('rows', [])
    return [r.get('signal_name', '') for r in rows if r.get('signal_name')]


def validate_rows_for_signal_type(signal_name: str) -> Tuple[bool, int, List[str]]:
    result = ws_query(f"""
        SELECT server_id, signal_name, score, evidence, scored_at
        FROM mcp_signal_scores
        WHERE signal_name = '{signal_name}'
        LIMIT 10
    """)
    rows = result.get('rows', [])
    
    if not rows:
        return False, 0, [f"No rows found for signal_name={signal_name}"]
    
    valid_count = 0
    errors = []
    
    for row in rows:
        is_valid, msg = validate_evidence_blob(row)
        if is_valid:
            valid_count += 1
        else:
            errors.append(msg)
    
    return valid_count > 0, valid_count, errors


def run_integration_test() -> Tuple[bool, List[str]]:
    all_signals = get_all_signal_types()
    
    if not all_signals:
        return False, ["No signal types found in mcp_signal_scores table"]
    
    print(f"Found {len(all_signals)} signal types: {all_signals}")
    
    required_signals = [
        'attestation_compliance',
        'ecosystem_reputation',
        'endpoint_security',
        'operational_health',
        'security_posture',
        'threat_intelligence'
    ]
    
    missing_signals = [s for s in required_signals if s not in all_signals]
    if missing_signals:
        print(f"WARNING: Missing expected signal types: {missing_signals}")
    
    all_passed = True
    all_errors = []
    signals_with_valid_rows = []
    
    for signal_name in all_signals:
        is_valid, count, errors = validate_rows_for_signal_type(signal_name)
        if is_valid:
            signals_with_valid_rows.append(signal_name)
            print(f"PASS: {signal_name} - {count} rows with valid evidence_blob")
        else:
            all_passed = False
            all_errors.extend(errors)
            print(f"FAIL: {signal_name} - {errors[0] if errors else 'Unknown error'}")
    
    if len(signals_with_valid_rows) < 8:
        all_passed = False
        all_errors.append(f"Expected rows from 8 signal types, got {len(signals_with_valid_rows)}: {signals_with_valid_rows}")
    
    return all_passed, all_errors


def check_analyser_recent_activity() -> Tuple[bool, str]:
    result = ws_query("""
        SELECT scored_at, COUNT(*) as cnt
        FROM mcp_signal_scores
        GROUP BY scored_at
        ORDER BY scored_at DESC
        LIMIT 5
    """)
    rows = result.get('rows', [])
    
    if not rows:
        return False, "No recent activity in mcp_signal_scores - analyser may not have run"
    
    latest_ts = rows[0].get('scored_at', '')
    latest_count = rows[0].get('cnt', 0)
    
    return True, f"Latest scored_at={latest_ts} with {latest_count} rows"


def main() -> int:
    print(f"[{SERVICE_NAME}] Starting integration verification...")
    
    total_rows_result = ws_query("SELECT COUNT(*) as total FROM mcp_signal_scores")
    total_rows = total_rows_result.get('rows', [{}])[0].get('total', 0)
    print(f"Total rows in mcp_signal_scores: {total_rows}")
    
    if total_rows == 0:
        print("FAIL: mcp_signal_scores table is empty")
        print("Signal analyser must run first to populate the table")
        return 1
    
    activity_ok, activity_msg = check_analyser_recent_activity()
    print(f"Activity check: {activity_msg}")
    
    passed, errors = run_integration_test()
    
    print(f"\n{'='*60}")
    print(f"Integration Test Result: {'PASS' if passed else 'FAIL'}")
    print(f"{'='*60}")
    
    if errors:
        print("\nErrors found:")
        for err in errors[:20]:
            print(f"  - {err}")
        if len(errors) > 20:
            print(f"  ... and {len(errors) - 20} more errors")
    
    return 0 if passed else 1


if __name__ == '__main__':
    sys.exit(main())