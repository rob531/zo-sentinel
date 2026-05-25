#!/usr/bin/env python3
"""
eval_529_builds.py - ZoComputer eval agent
Reads build_registry.json, runs smoke tests on every 'ok' build,
produces quality report at shared/outputs/eval_529/report.json

Designed to be triggered by Tower probe or run directly.
Idempotent - skips files already in report.
"""
import json, subprocess, sys, os, time
from pathlib import Path
from datetime import datetime, timezone

REGISTRY  = Path('/home/workspace/zo_sentinel/.build_registry.json')
OUT_DIR   = Path('/home/workspace/shared/outputs/eval_529')
LOG_FILE  = Path('/home/workspace/logs/eval_529.log')
ZO_DIR    = Path('/home/workspace/zo_sentinel')

OUT_DIR.mkdir(parents=True, exist_ok=True)

def log(msg):
    ts = datetime.now(timezone.utc).isoformat()
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    with open(LOG_FILE, 'a') as f: f.write(line + '\n')

def smoke_test(filepath: str) -> dict:
    """Run a file through 3 checks. Returns result dict."""
    result = {'file': filepath, 'checks': {}}
    path = Path(filepath)

    # Check 1: file exists
    if not path.exists():
        result['verdict'] = 'MISSING'
        result['checks']['exists'] = False
        return result
    result['checks']['exists'] = True

    # Check 2: syntax check
    r = subprocess.run(
        ['python3', '-m', 'py_compile', str(path)],
        capture_output=True, text=True, timeout=10
    )
    result['checks']['syntax_ok'] = (r.returncode == 0)
    if r.returncode != 0:
        result['checks']['syntax_error'] = r.stderr[:200]
        result['verdict'] = 'SYNTAX_FAIL'
        return result

    # Check 3: import check (catches missing deps, broken imports)
    r2 = subprocess.run(
        ['python3', '-c',
         f'import sys; sys.path.insert(0, "{ZO_DIR}"); '
         f'import importlib.util; '
         f'spec=importlib.util.spec_from_file_location("m", "{path}"); '
         f'm=importlib.util.module_from_spec(spec); spec.loader.exec_module(m)'],
        capture_output=True, text=True, timeout=15,
        cwd=str(ZO_DIR)
    )
    result['checks']['imports_ok'] = (r2.returncode == 0)
    if r2.returncode != 0:
        err = r2.stderr[-300:]
        result['checks']['import_error'] = err
        # Classify error type
        if 'ModuleNotFoundError' in err or 'ImportError' in err:
            result['verdict'] = 'IMPORT_FAIL'
        elif 'stub' in err.lower() or 'not implemented' in err.lower():
            result['verdict'] = 'STUB'
        else:
            result['verdict'] = 'RUNTIME_FAIL'
        return result

    # Check 4: stub detection (file exists, imports OK but is hollow)
    content = path.read_text()
    lines = [l.strip() for l in content.split('\n') if l.strip() and not l.strip().startswith('#')]
    code_lines = len(lines)
    has_pass_only = all(l in ('pass', 'return', 'return None', '...') for l in lines if l not in ('"""', "'''"))
    
    result['checks']['code_lines'] = code_lines
    result['checks']['stub_risk'] = code_lines < 15 or has_pass_only
    
    if code_lines < 8:
        result['verdict'] = 'STUB'
    else:
        result['verdict'] = 'OK'

    return result


def run():
    log('=== eval_529 starting ===')
    
    if not REGISTRY.exists():
        log(f'ERROR: registry not found at {REGISTRY}')
        sys.exit(1)
    
    registry = json.loads(REGISTRY.read_text())
    log(f'Registry entries: {len(registry)}')
    
    # Load existing report for idempotency
    report_path = OUT_DIR / 'report.json'
    if report_path.exists():
        report = json.loads(report_path.read_text())
        existing = {r['file'] for r in report.get('results', [])}
        log(f'Resuming: {len(existing)} already evaluated')
    else:
        report = {'started_at': datetime.now(timezone.utc).isoformat(), 'results': []}
        existing = set()
    
    results = report['results']
    total = len([v for v in registry.values() if v.get('status') == 'ok'])
    done = 0
    
    for key, entry in registry.items():
        if entry.get('status') != 'ok':
            continue
        filepath = entry.get('file', '')
        if filepath in existing:
            continue
        
        try:
            result = smoke_test(filepath)
            result['task'] = entry.get('task', '')
            result['built_at'] = entry.get('built_at', '')
            result['size'] = entry.get('size', 0)
            results.append(result)
            done += 1
            log(f'[{done}/{total}] {entry["task"]}: {result["verdict"]}')
            
            # Save incrementally every 20
            if done % 20 == 0:
                report['results'] = results
                report_path.write_text(json.dumps(report, indent=2))
                log(f'Checkpoint saved ({done} evaluated)')
        except Exception as e:
            log(f'Error on {filepath}: {e}')
            results.append({'file': filepath, 'task': entry.get('task',''), 
                           'verdict': 'ERROR', 'error': str(e)})
        
        time.sleep(0.1)  # don't hammer filesystem
    
    # Final report
    verdicts = {}
    for r in results:
        v = r.get('verdict', 'UNKNOWN')
        verdicts[v] = verdicts.get(v, 0) + 1
    
    report['completed_at'] = datetime.now(timezone.utc).isoformat()
    report['results'] = results
    report['summary'] = verdicts
    report['total_evaluated'] = len(results)
    report_path.write_text(json.dumps(report, indent=2))
    
    # Write human-readable summary
    summary_lines = [
        f'eval_529 Report — {datetime.now(timezone.utc).isoformat()}',
        f'Total evaluated: {len(results)}',
        '',
        'Verdicts:'
    ]
    for v, count in sorted(verdicts.items(), key=lambda x: -x[1]):
        pct = count * 100 // max(len(results), 1)
        summary_lines.append(f'  {v:20s} {count:4d}  ({pct}%)')
    
    summary_lines += ['', 'Files needing attention (non-OK):']
    for r in results:
        if r.get('verdict') not in ('OK', None):
            err = r.get('checks', {}).get('import_error', '') or r.get('checks', {}).get('syntax_error', '')
            summary_lines.append(f'  [{r["verdict"]}] {r["task"]} — {err[:80]}')
    
    summary = '\n'.join(summary_lines)
    (OUT_DIR / 'summary.txt').write_text(summary)
    log('Summary written to shared/outputs/eval_529/summary.txt')
    print(summary)
    log('=== eval_529 complete ===')


if __name__ == '__main__':
    run()