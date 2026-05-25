import re
import sys
import json
import traceback
from pathlib import Path
from datetime import datetime
from collections import defaultdict
from typing import Optional


def parse_smoke_log(log_path: str) -> list[dict]:
    """Parse smoke test log file and extract failure information."""
    failures = []
    current_failure = {}
    
    with open(log_path, 'r') as f:
        content = f.read()
    
    # Split by failure markers
    failure_pattern = r'TEST FAILED:\s*(.+?)(?=\n(?:PASS|FAIL|===|$))'
    traceback_pattern = r'Traceback \(most recent call last\):\n(.*?)(?=\n\S|\Z)'
    import_error_pattern = r'File "<string>", line \d+|File ".*?", line \d+'
    
    blocks = re.split(r'={20,}', content)
    
    for block in blocks:
        if 'FAILED' in block or 'ERROR' in block:
            # Extract test name
            name_match = re.search(r'(?:FAILED|TEST):\s*(.+)', block)
            test_name = name_match.group(1).strip() if name_match else 'unknown'
            
            # Extract traceback
            tb_match = re.search(r'Traceback \(most recent call last\):\n(.*?)(?=\n\n|\Z)', block, re.DOTALL)
            traceback_text = tb_match.group(1).strip() if tb_match else ''
            
            # Extract import-related errors specifically
            import_errors = re.findall(
                r'File "([^"]+)", line (\d+).*?in\s+<module>|'
                r'File "<string>", line (\d+).*?in\s+<module>|'
                r'ImportError:?\s*(.+?)(?:\n|$)|'
                r'ModuleNotFoundError:?\s*(.+?)(?:\n|$)',
                traceback_text,
                re.DOTALL
            )
            
            # Check for line 10 import failures
            line_10_errors = []
            for match in re.finditer(r'line (\d+)', traceback_text):
                if match.group(1) == '10':
                    line_10_errors.append(match.group(0))
            
            current_failure = {
                'test_name': test_name,
                'traceback': traceback_text,
                'import_errors': import_errors,
                'line_10_errors': line_10_errors,
                'has_import_chain_failure': '<string>' in traceback_text or 'in <module>' in traceback_text,
                'timestamp': datetime.now().isoformat()
            }
            failures.append(current_failure)
    
    return failures


def identify_failing_modules(failures: list[dict]) -> dict:
    """Identify which modules are causing import chain failures."""
    module_failures = defaultdict(list)
    
    for failure in failures:
        for name in ['registry_api.py', 'rug_pull_monitor.py', 'signal_analyser.py']:
            if name in failure['test_name'] or name in failure['traceback']:
                module_failures[name].append(failure)
    
    return dict(module_failures)


def extract_import_chain(traceback_text: str) -> list[dict]:
    """Extract the import chain from traceback."""
    chain = []
    
    # Match file references in traceback
    file_pattern = r'File "([^"]+)", line (\d+)(?:, in ([^"\n]+))?\n\s+(.+)'
    
    for match in re.finditer(file_pattern, traceback_text):
        chain.append({
            'file': match.group(1),
            'line': int(match.group(2)),
            'in_func': match.group(3),
            'code': match.group(4).strip()
        })
    
    return chain


def diagnose_import_chain_failure(failure: dict) -> dict:
    """Diagnose the specific import chain failure."""
    diagnosis = {
        'test_name': failure['test_name'],
        'possible_causes': [],
        'recommendations': [],
        'confidence': 'low'
    }
    
    tb = failure['traceback']
    
    # Check for circular imports
    if failure['line_10_errors']:
        diagnosis['possible_causes'].append('Line 10 import statement failure - likely circular import or missing dependency')
        diagnosis['confidence'] = 'high'
    
    # Check for <string> placeholder
    if '<string>' in tb:
        diagnosis['possible_causes'].append('Dynamic import via exec/eval - import chain not fully traced')
        diagnosis['recommendations'].append('Check for exec() or eval() calls in import chain')
    
    # Check for ModuleNotFoundError
    if 'ModuleNotFoundError' in tb:
        diagnosis['possible_causes'].append('Missing module in Python path')
        diagnosis['recommendations'].append('Verify all dependencies are installed and in PYTHONPATH')
    
    # Check for ImportError
    if 'ImportError' in tb:
        diagnosis['possible_causes'].append('Import statement failed - possibly syntax error in module')
        diagnosis['recommendations'].append('Check syntax of imported module')
    
    # Extract and analyze import chain
    chain = extract_import_chain(tb)
    if chain:
        diagnosis['import_chain'] = chain
        # Check if cycle exists
        modules = [c['file'] for c in chain if not c['file'].startswith('<')]
        if len(modules) != len(set(modules)):
            diagnosis['possible_causes'].append('Circular dependency detected in import chain')
            diagnosis['recommendations'].append('Review import order in affected modules')
    
    return diagnosis


def run_diagnostic(log_path: Optional[str] = None) -> dict:
    """Run the full import chain diagnostic."""
    if log_path is None:
        log_path = '/home/workspace/zo_sentinel/logs/smoke_test.log'
    
    results = {
        'timestamp': datetime.now().isoformat(),
        'log_file': log_path,
        'summary': {
            'total_failures': 0,
            'import_chain_failures': 0,
            'affected_modules': []
        },
        'failures': [],
        'diagnoses': []
    }
    
    # Check if log file exists
    log_file = Path(log_path)
    if not log_file.exists():
        results['error'] = f'Log file not found: {log_path}'
        return results
    
    # Parse failures
    failures = parse_smoke_log(log_path)
    results['summary']['total_failures'] = len(failures)
    
    # Identify import chain failures
    import_chain_failures = [f for f in failures if f['has_import_chain_failure']]
    results['summary']['import_chain_failures'] = len(import_chain_failures)
    
    # Identify affected modules
    affected = identify_failing_modules(failures)
    results['summary']['affected_modules'] = list(affected.keys())
    
    # Store failures
    results['failures'] = failures
    
    # Run diagnosis on each failure
    for failure in import_chain_failures:
        diagnosis = diagnose_import_chain_failure(failure)
        results['diagnoses'].append(diagnosis)
    
    return results


def print_report(results: dict) -> None:
    """Print a human-readable diagnostic report."""
    print("=" * 60)
    print("ZO-SENTINEL: Import Chain Diagnostic Report")
    print("=" * 60)
    print(f"Timestamp: {results['timestamp']}")
    print(f"Log File: {results['log_file']}")
    print()
    print("SUMMARY")
    print("-" * 40)
    print(f"Total Failures: {results['summary']['total_failures']}")
    print(f"Import Chain Failures: {results['summary']['import_chain_failures']}")
    print(f"Affected Modules: {', '.join(results['summary']['affected_modules']) or 'None'}")
    print()
    
    if results['diagnoses']:
        print("DIAGNOSES")
        print("-" * 40)
        for i, diag in enumerate(results['diagnoses'], 1):
            print(f"\n[{i}] Test: {diag['test_name']}")
            print(f"    Confidence: {diag['confidence']}")
            print(f"    Possible Causes:")
            for cause in diag['possible_causes']:
                print(f"      - {cause}")
            if diag['recommendations']:
                print(f"    Recommendations:")
                for rec in diag['recommendations']:
                    print(f"      - {rec}")
            if 'import_chain' in diag:
                print(f"    Import Chain:")
                for item in diag['import_chain']:
                    print(f"      {item['file']}:{item['line']} in {item['in_func'] or '<module>'}")
    
    print()
    print("=" * 60)


def save_results(results: dict, output_path: Optional[str] = None) -> None:
    """Save diagnostic results to JSON file."""
    if output_path is None:
        output_path = '/home/workspace/zo_sentinel/logs/import_chain_diag.json'
    
    with open(output_path, 'w') as f:
        json.dump(results, f, indent=2, default=str)


def main():
    import argparse
    
    parser = argparse.ArgumentParser(description='ZO-SENTINEL Import Chain Diagnostic')
    parser.add_argument('--log', '-l', default='/home/workspace/zo_sentinel/logs/smoke_test.log',
                        help='Path to smoke test log file')
    parser.add_argument('--output', '-o', 
                        default='/home/workspace/zo_sentinel/logs/import_chain_diag.json',
                        help='Output path for JSON results')
    parser.add_argument('--json', '-j', action='store_true',
                        help='Output only JSON format')
    
    args = parser.parse_args()
    
    results = run_diagnostic(args.log)
    
    if args.json:
        print(json.dumps(results, indent=2, default=str))
    else:
        print_report(results)
    
    save_results(results, args.output)
    
    return 0 if results['summary']['import_chain_failures'] == 0 else 1


if __name__ == '__main__':
    sys.exit(main())