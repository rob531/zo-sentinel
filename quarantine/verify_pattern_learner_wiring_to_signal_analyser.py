#!/usr/bin/env python3
"""
Verifier: pattern_learner wiring to signal_analyser

Checks whether pattern_learner.py is properly wired into signal_analyser.py:
- Imports of pattern_learner modules
- Calls to pattern_learner functions in process_server() or compute_composite_score()
- Existence of mcp_decisions for pattern_learner to learn from
- Pattern_learner knowledge output accessibility

Exit codes: 0 = wired, 1 = unwired/partially_wired
"""

import os
import re
import sys


def read_source_file(filepath: str) -> str:
    """Read source file contents."""
    if not os.path.exists(filepath):
        return ""
    with open(filepath, 'r', encoding='utf-8') as f:
        return f.read()


def check_imports(source: str) -> dict:
    """Check for pattern_learner imports."""
    imports = {
        'pattern_learner': False,
        'pattern_learner_feedback': False,
        'from_pattern_learner': [],
        'import_pattern_learner': []
    }
    
    # Check for import pattern_learner
    if re.search(r'\bimport\s+pattern_learner\b', source):
        imports['pattern_learner'] = True
        imports['import_pattern_learner'].append('pattern_learner')
    
    # Check for from pattern_learner import
    from_matches = re.findall(r'from\s+pattern_learner\s+import\s+([^\n;]+)', source)
    for match in from_matches:
        imports['from_pattern_learner'].extend([m.strip() for m in match.split(',')])
    
    # Check for pattern_learner_feedback imports
    if re.search(r'\bimport\s+pattern_learner_feedback\b', source):
        imports['pattern_learner_feedback'] = True
        imports['import_pattern_learner'].append('pattern_learner_feedback')
    
    from_fb_matches = re.findall(r'from\s+pattern_learner_feedback\s+import\s+([^\n;]+)', source)
    for match in from_fb_matches:
        imports['from_pattern_learner'].extend([m.strip() for m in match.split(',')])
    
    return imports


def check_function_calls(source: str) -> dict:
    """Check for pattern_learner function calls in key functions."""
    calls = {
        'process_server': [],
        'compute_composite_score': [],
        'other': [],
        'any_call': False
    }
    
    # Pattern_learner functions to look for
    learner_functions = [
        'ws_query', 'ws_write', 'run_learning_cycle', 
        'learn_from_decision', 'get_patterns', 'apply_patterns',
        'query', 'write', 'get_learned_patterns', 'apply_learned_patterns',
        'get_rejection_decisions', 'get_approval_decisions', 
        'analyze_rejection_patterns', 'load_current_knowledge_base'
    ]
    
    # Find function definitions
    func_pattern = re.compile(r'def\s+(process_server|compute_composite_score|analyze_signal)\s*\(')
    functions_found = {}
    for match in func_pattern.finditer(source):
        func_name = match.group(1)
        start = match.start()
        # Find the end of this function (next def or end of class/module)
        next_def = re.search(r'\ndef\s+\w+\s*\(', source[start+10:])
        if next_def:
            end = start + 10 + next_def.start()
        else:
            end = len(source)
        functions_found[func_name] = source[start:end]
    
    # Check for calls to pattern_learner functions
    for func_name, func_body in functions_found.items():
        for learner_fn in learner_functions:
            if re.search(rf'\b{learner_fn}\s*\(', func_body):
                calls[func_name].append(learner_fn)
                calls['any_call'] = True
    
    # Also do a general scan for any pattern_learner calls
    general_calls = re.findall(r'pattern_learner[a-zA-Z_]*\.\w+\s*\(', source)
    general_calls += re.findall(r'\b(ws_query|ws_write|run_learning_cycle|learn_from_decision|get_patterns)\s*\(', source)
    
    for call in general_calls:
        calls['other'].append(call)
    
    return calls


def check_mcp_decisions_exist(write_service_url: str = None) -> dict:
    """Query for existence of mcp_decisions for pattern_learner to learn from."""
    result = {
        'decisions_exist': False,
        'decision_count': 0,
        'query_attempted': False,
        'error': None
    }
    
    # Try to query write_service /query endpoint
    if write_service_url:
        try:
            import requests
            result['query_attempted'] = True
            response = requests.post(
                f"{write_service_url}/query",
                json={"sql": "SELECT COUNT(*) as cnt FROM mcp_decisions"},
                timeout=10
            )
            if response.status_code == 200:
                data = response.json()
                rows = data.get('rows', [])
                if rows:
                    result['decision_count'] = rows[0].get('cnt', 0)
                result['decisions_exist'] = result['decision_count'] > 0
            else:
                result['error'] = f"HTTP {response.status_code}"
        except Exception as e:
            result['error'] = str(e)
    
    # Alternative: check for local knowledge files
    knowledge_paths = [
        'pattern_learner_knowledge.json',
        'knowledge/pattern_learner.json',
        '.pattern_learner_knowledge'
    ]
    for path in knowledge_paths:
        if os.path.exists(path):
            result['decisions_exist'] = True
            result['knowledge_file_found'] = path
            break
    
    return result


def check_pattern_learner_knowledge(write_service_url: str = None) -> dict:
    """Check if pattern_learner has accessible knowledge output."""
    result = {
        'knowledge_accessible': False,
        'knowledge_source': None,
        'checked': False
    }
    
    # Check for knowledge files
    knowledge_paths = [
        'pattern_learner_knowledge.json',
        'knowledge/pattern_learner.json',
        'knowledge/decisions.json',
        '.pattern_learner_knowledge',
        'data/pattern_learner_knowledge.json',
        'KNOWLEDGE_BASE.md'
    ]
    
    for path in knowledge_paths:
        if os.path.exists(path):
            result['knowledge_accessible'] = True
            result['knowledge_source'] = f"file:{path}"
            result['checked'] = True
            return result
    
    # Try to query write_service for pattern knowledge
    if write_service_url:
        result['checked'] = True
        try:
            import requests
            response = requests.post(
                f"{write_service_url}/query",
                json={"sql": "SELECT COUNT(*) as cnt FROM pattern_learner_knowledge"},
                timeout=10
            )
            if response.status_code == 200:
                result['knowledge_accessible'] = True
                result['knowledge_source'] = "write_service:pattern_learner_knowledge"
        except Exception:
            pass
    
    return result


def determine_wiring_status(imports: dict, calls: dict, decisions: dict, knowledge: dict) -> tuple:
    """Determine overall wiring status."""
    
    has_imports = imports['pattern_learner'] or imports['pattern_learner_feedback'] or len(imports['from_pattern_learner']) > 0
    has_function_calls = len(calls['process_server']) > 0 or len(calls['compute_composite_score']) > 0 or calls['any_call']
    has_decisions = decisions['decisions_exist']
    has_knowledge = knowledge['knowledge_accessible']
    
    if has_imports and has_function_calls and has_decisions:
        return 'wired', "All checks passed"
    elif has_imports and has_function_calls:
        return 'partially_wired', "Imports and calls present, but no feedback data found"
    elif has_imports:
        return 'partially_wired', "Imports present but no function calls in process_server/compute_composite_score"
    else:
        gaps = []
        if not has_imports:
            gaps.append("No pattern_learner imports found")
        if not has_function_calls:
            gaps.append("No pattern_learner function calls found in process_server/compute_composite_score")
        if not has_decisions:
            gaps.append("No mcp_decisions found for pattern_learner to learn from")
        return 'unwired', "; ".join(gaps)


def run():
    """Main verification routine."""
    
    print("=" * 70)
    print("PATTERN_LEARNER WIRING VERIFIER")
    print("=" * 70)
    print()
    
    # Find signal_analyser.py
    script_dir = os.path.dirname(os.path.abspath(__file__))
    signal_analyser_path = os.path.join(script_dir, 'signal_analyser.py')
    
    # Also check parent directories
    if not os.path.exists(signal_analyser_path):
        alt_paths = [
            os.path.join(script_dir, '..', 'signal_analyser.py'),
            os.path.join(script_dir, '..', '..', 'signal_analyser.py'),
        ]
        for path in alt_paths:
            if os.path.exists(path):
                signal_analyser_path = path
                break
    
    print(f"[CHECK 1] Reading signal_analyser.py: {signal_analyser_path}")
    source = read_source_file(signal_analyser_path)
    
    if not source:
        print(f"  ERROR: Could not read {signal_analyser_path}")
        print("  RESULT: FAIL - signal_analyser.py not found")
        print()
        print("Gap: signal_analyser.py does not exist")
        sys.exit(1)
    
    print(f"  Read {len(source)} characters")
    print()
    
    # Check 2: Imports
    print("[CHECK 2] Checking for pattern_learner imports...")
    imports = check_imports(source)
    
    if imports['pattern_learner']:
        print("  FOUND: import pattern_learner")
    if imports['pattern_learner_feedback']:
        print("  FOUND: import pattern_learner_feedback")
    if imports['from_pattern_learner']:
        print(f"  FOUND: from pattern_learner import {', '.join(imports['from_pattern_learner'])}")
    if imports['import_pattern_learner'] or imports['from_pattern_learner']:
        print("  STATUS: Import checks PASS")
    else:
        print("  STATUS: No pattern_learner imports found")
    print()
    
    # Check 3: Function calls
    print("[CHECK 3] Checking for pattern_learner function calls...")
    calls = check_function_calls(source)
    
    if calls['process_server']:
        print(f"  FOUND in process_server(): {', '.join(calls['process_server'])}")
    if calls['compute_composite_score']:
        print(f"  FOUND in compute_composite_score(): {', '.join(calls['compute_composite_score'])}")
    if calls['other']:
        print(f"  FOUND elsewhere: {', '.join(set(calls['other']))}")
    
    if calls['any_call']:
        print("  STATUS: Function call checks PASS")
    else:
        print("  STATUS: No pattern_learner function calls found in key functions")
    print()
    
    # Check 4: MCP decisions existence
    print("[CHECK 4] Checking for mcp_decisions data...")
    decisions = check_mcp_decisions_exist()
    
    if decisions['decisions_exist']:
        count = decisions.get('decision_count', 'unknown')
        print(f"  FOUND: mcp_decisions exist (count: {count})")
        print("  STATUS: Decision data checks PASS")
    else:
        if decisions['query_attempted'] and decisions['error']:
            print(f"  QUERY ERROR: {decisions['error']}")
        print("  STATUS: No mcp_decisions found (pattern_learner has no feedback data)")
    print()
    
    # Check 5: Pattern learner knowledge
    print("[CHECK 5] Checking pattern_learner knowledge output...")
    knowledge = check_pattern_learner_knowledge()
    
    if knowledge['knowledge_accessible']:
        print(f"  FOUND: {knowledge['knowledge_source']}")
        print("  STATUS: Knowledge output accessible")
    else:
        print("  NOTE: No pattern_learner knowledge output found (may be first run)")
    print()
    
    # Determine overall status
    print("[RESULT] Determining overall wiring status...")
    status, message = determine_wiring_status(imports, calls, decisions, knowledge)
    
    print()
    print("=" * 70)
    print("VERIFICATION REPORT")
    print("=" * 70)
    print()
    print(f"  wiring_status: {status}")
    print(f"  details: {message}")
    print()
    
    # Detailed breakdown
    print("Breakdown:")
    print(f"  - pattern_learner imports: {'YES' if imports['pattern_learner'] or imports['from_pattern_learner'] else 'NO'}")
    print(f"  - Function calls in process_server/compute_composite_score: {'YES' if calls['process_server'] or calls['compute_composite_score'] else 'NO'}")
    print(f"  - Any pattern_learner calls: {'YES' if calls['any_call'] else 'NO'}")
    print(f"  - mcp_decisions exist: {'YES' if decisions['decisions_exist'] else 'NO'}")
    print(f"  - Knowledge output accessible: {'YES' if knowledge['knowledge_accessible'] else 'NO'}")
    print()
    
    if status == 'wired':
        print("PASS - pattern_learner is properly wired into signal_analyser")
        print()
        print("Feedback loop confirmed:")
        print("  analyst_decisions -> pattern_learner -> signal_scoring")
        sys.exit(0)
    elif status == 'partially_wired':
        print("PARTIAL - pattern_learner is partially wired")
        print()
        print("Gap description:", message)
        sys.exit(1)
    else:
        print("FAIL - pattern_learner is not wired into signal_analyser")
        print()
        print("Gap description:", message)
        sys.exit(1)


if __name__ == '__main__':
    run()