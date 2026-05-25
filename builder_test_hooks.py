#!/usr/bin/env python3
"""
builder_test_hooks.py v3 -- Added on_queue_empty exponential backoff.

v3 changes:
  - on_queue_empty: only runs full L1-L4 when new files were built this cycle.
    If queue empty AND nothing built: runs L1 smoke only, then sleeps 30min.
    Prevents CPU burn when system is healthy and waiting for new directives.
"""
import sys, os, ast, json, logging, glob, subprocess, requests, time
from datetime import datetime, timezone
from pathlib import Path

PROJECT_DIR   = Path('/home/workspace/zo_sentinel')
TEST_DIR      = PROJECT_DIR / 'tests'
DIRECTIVE_DIR = PROJECT_DIR / 'directives'
WRITE_SERVICE = 'http://127.0.0.1:8772'

FIX_ATTEMPTS_PATH = DIRECTIVE_DIR / 'fix_attempts.json'
PAUSED_FILES_PATH = DIRECTIVE_DIR / 'paused_files.json'
MAX_RETRIES = 3

# Backoff state -- persists in module scope across cycles
_last_full_test_at = 0       # epoch seconds of last full L1-L4 run
_idle_cycles = 0             # consecutive empty-queue cycles with no builds
FULL_TEST_MIN_INTERVAL = 1800  # minimum 30 min between full test runs when idle

SUPERVISORD_DAEMONS = {
    'write_service', 'inference_router', 'pipeline_bridge',
    'manager_agent', 'anti_entropy_daemon', 'world_article_feeder',
    'wisdom_synthesiser',
}

log = logging.getLogger('builder')

if str(TEST_DIR) not in sys.path:
    sys.path.insert(0, str(TEST_DIR))


def strip_traceback(detail: str) -> str:
    if not detail or len(detail) < 50:
        return detail
    lines = [l.rstrip() for l in detail.split('\n') if l.strip()]
    if len(lines) <= 4:
        return detail
    exception_line = ''
    for line in reversed(lines):
        if ': ' in line and not line.startswith(' ') and not line.startswith('File'):
            exception_line = line
            break
    if not exception_line:
        exception_line = lines[-1]
    file_ref = ''
    for line in reversed(lines):
        if line.strip().startswith('File "') and 'line' in line:
            file_ref = line.strip()
            break
    context_lines = [l for l in lines[-6:-1] if l.strip()][-3:]
    stripped = exception_line
    if file_ref:
        stripped = file_ref + ' -- ' + exception_line
    if context_lines:
        stripped += ' | context: ' + ' | '.join(context_lines)
    return stripped[:400]


def _load_fix_attempts() -> dict:
    try:
        if FIX_ATTEMPTS_PATH.exists():
            return json.loads(FIX_ATTEMPTS_PATH.read_text())
    except Exception:
        pass
    return {}

def _save_fix_attempts(attempts: dict):
    FIX_ATTEMPTS_PATH.parent.mkdir(exist_ok=True)
    FIX_ATTEMPTS_PATH.write_text(json.dumps(attempts, indent=2))

def _load_paused() -> set:
    try:
        if PAUSED_FILES_PATH.exists():
            return set(json.loads(PAUSED_FILES_PATH.read_text()).get('paused', []))
    except Exception:
        pass
    return set()

def _pause_file(fname: str, reason: str):
    paused = _load_paused()
    paused.add(fname)
    PAUSED_FILES_PATH.write_text(json.dumps({
        'paused': sorted(paused),
        'note': 'Files paused due to oscillation. Manual review required.',
        'updated': datetime.now(timezone.utc).isoformat()
    }, indent=2))
    log.error('[hooks] OSCILLATION: %s paused after %d retries -- %s', fname, MAX_RETRIES, reason)
    _ws_write('mesh_events', {
        'agent_id': 'zo_sentinel.builder_test_hooks',
        'event_type': 'oscillation_detected', 'tier': 'T1',
        'payload': json.dumps({'file': fname, 'reason': reason}),
        'severity': 'CRITICAL',
        'created_at': datetime.now(timezone.utc).isoformat()
    })


def _ws_write(table, row):
    try:
        requests.post(WRITE_SERVICE + '/write',
            json={'table': table, 'rows': row, 'wait': True}, timeout=5)
    except Exception:
        pass

def _emit_event(event_type, payload, severity='INFO'):
    _ws_write('mesh_events', {
        'agent_id': 'zo_sentinel.builder_test_hooks',
        'event_type': event_type, 'tier': 'T1',
        'payload': json.dumps(payload), 'severity': severity,
        'created_at': datetime.now(timezone.utc).isoformat()
    })

def _fresh_results():
    return {'passed': [], 'failed': [], 'skipped': [], 'started_at': None, 'level_reached': 0}

def _import_runner():
    try:
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            'zo_sentinel_test_runner',
            str(TEST_DIR / 'zo_sentinel_test_runner.py')
        )
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    except Exception as e:
        log.warning('[hooks] test runner import failed: %s', e)
        return None


SMOKE_ANTIPATTERNS = [
    ("write_service(",     "write_service() called as function"),
    ("'row':",             "'row' key -- expects 'rows'"),
    ("from duckdb import", "forbidden duckdb import"),
    ("duckdb.connect(",    "direct duckdb.connect()"),
]

MODULE_CONTRACTS = {
    'search_api.py':        {'must_contain': ['.split('], 'reason': 'multi-term search'},
    'registry_api.py':      {'must_contain': ['/health'], 'reason': 'health route'},
    'approval_workflow.py': {'must_contain': ['/health', 'BaseModel'], 'reason': 'health+validation'},
}

def _smoke_file(fpath):
    if not fpath.exists(): return False, 'not found'
    try: content = fpath.read_text()
    except Exception as e: return False, f'read error: {e}'
    if len(content) < 300: return False, f'stub ({len(content)}b)'
    try: ast.parse(content)
    except SyntaxError as e: return False, f'SyntaxError line {e.lineno}: {e.msg}'
    for pat, msg in SMOKE_ANTIPATTERNS:
        if pat in content: return False, f'wiring: {msg}'
    for needle in MODULE_CONTRACTS.get(fpath.name, {}).get('must_contain', []):
        if needle not in content:
            return False, f'missing "{needle}" -- {MODULE_CONTRACTS[fpath.name]["reason"]}'
    return True, f'{len(content)}b'


PHASE_FILES = {
    '2':  ['schema.py', 'schema_v2.py', 'db_utils.py'],
    '3':  ['signal_analyser.py', 'trust_synthesiser.py', 'verdict_taxonomy.py'],
    '4':  ['policy_engine.py', 'approval_workflow.py'],
    '5':  ['rug_pull_monitor.py'],
    '6':  ['registry_api.py'],
    '7':  ['risk_ranker.py', 'attestation_engine.py'],
    '8':  ['search_api.py'],
}


def on_phase_checkpoint(phase, passed_files, failed_files):
    phase_str = str(phase)
    target_files = PHASE_FILES.get(phase_str, [])
    if not target_files: return {'phase': phase_str, 'skipped': True}
    smoke_pass, smoke_fail, fix_queued = [], [], []
    for fname in target_files:
        fpath = PROJECT_DIR / fname
        ok, reason = _smoke_file(fpath)
        if ok: smoke_pass.append(fname)
        else:
            smoke_fail.append({'file': fname, 'reason': reason})
            log.warning('[phase%s-test] FAIL %s: %s', phase_str, fname, reason)
            if _queue_smoke_fix(fname, reason): fix_queued.append(fname)
    status = 'PASS' if not smoke_fail else f'FAIL ({len(smoke_fail)}/{len(target_files)})'
    log.info('[phase%s-test] %s  smoke: %d pass, %d fail', phase_str, status, len(smoke_pass), len(smoke_fail))
    _emit_event('phase_smoke_complete', {
        'phase': phase_str, 'status': status,
        'passed': smoke_pass, 'failed': [f['file'] for f in smoke_fail],
        'fixes_queued': fix_queued
    }, severity='INFO' if not smoke_fail else 'WARNING')
    return {'phase': phase_str, 'status': status, 'pass': len(smoke_pass),
            'fail': len(smoke_fail), 'fixes_queued': len(fix_queued)}


# ---------------------------------------------------------------------------
# Hook 2: on_queue_empty -- with exponential backoff
# ---------------------------------------------------------------------------

def on_queue_empty(cycle_count=0, builds_total=0):
    global _last_full_test_at, _idle_cycles

    now = time.time()
    time_since_full_test = now - _last_full_test_at
    new_files_this_cycle = builds_total > 0  # did we actually build anything?

    # IDLE PATH: queue empty, nothing built, full test ran recently
    if not new_files_this_cycle and time_since_full_test < FULL_TEST_MIN_INTERVAL:
        _idle_cycles += 1
        wait_secs = min(1800, 300 * _idle_cycles)  # 5min, 10min, 15min... cap 30min
        log.info('[E2E-TEST] Idle cycle %d -- nothing built, full test ran %dm ago. '
                 'L1 smoke only, next check in %dm.',
                 _idle_cycles, int(time_since_full_test/60), int(wait_secs/60))

        # L1 smoke only -- fast, low CPU
        runner = _import_runner()
        if runner:
            runner.results.update(_fresh_results())
            runner.run_level1_smoke()
            fail_count = len(runner.results['failed'])
            pass_count = len(runner.results['passed'])
            log.info('[E2E-TEST] L1 only: %d pass, %d fail', pass_count, fail_count)
            if fail_count > 0:
                # Something broke -- escalate to full test immediately
                log.warning('[E2E-TEST] L1 failures detected -- escalating to full suite')
                new_files_this_cycle = True  # fall through to full test below
            else:
                return {'status': 'IDLE_L1_PASS', 'passed': pass_count,
                        'idle_cycles': _idle_cycles, 'next_full_test_in_min': int(wait_secs/60)}

    # ACTIVE PATH: new files built, or L1 failures, or 30min interval expired
    _idle_cycles = 0
    _last_full_test_at = now

    log.info('='*60)
    log.info('[E2E-TEST] Full L1-L4 suite -- %d new builds, %dm since last full test',
             builds_total, int(time_since_full_test/60))
    log.info('='*60)

    runner = _import_runner()
    if not runner:
        log.warning('[E2E-TEST] test runner unavailable')
        return {'skipped': True}

    runner.results.update(_fresh_results())
    runner.results['started_at'] = datetime.now(timezone.utc).isoformat()

    runner.run_level1_smoke()
    l1 = len(runner.results['failed'])
    log.info('[E2E-TEST] L1 smoke: %d pass, %d fail', len(runner.results['passed']), l1)

    runner.run_level2_phases()
    l2 = len(runner.results['failed']) - l1
    log.info('[E2E-TEST] L2 phase: +%d fail', l2)

    runner.run_level3_pipeline(cleanup=True)
    l3 = len(runner.results['failed']) - l1 - l2
    log.info('[E2E-TEST] L3 pipeline: +%d fail', l3)

    runner.run_level4_e2e()
    l4 = len(runner.results['failed']) - l1 - l2 - l3
    log.info('[E2E-TEST] L4 e2e: +%d fail', l4)

    total_pass = len(runner.results['passed'])
    total_fail = len(runner.results['failed'])
    total_skip = len(runner.results['skipped'])

    for failure in runner.results['failed']:
        if 'detail' in failure:
            failure['detail'] = strip_traceback(failure['detail'])

    try: runner.save_results()
    except Exception: pass

    fixes = 0
    try: fixes = runner.emit_fix_directives(write_db=True)
    except Exception as e: log.warning('[E2E-TEST] emit_fix_directives: %s', e)

    status = 'ALL_PASS' if total_fail == 0 else f'{total_fail}_FAILURES'
    log.info('[E2E-TEST] %s: %d pass, %d fail, %d skip, %d fixes queued',
             status, total_pass, total_fail, total_skip, fixes)

    _emit_event('queue_empty_test_complete', {
        'status': status, 'passed': total_pass, 'failed': total_fail,
        'skipped': total_skip, 'fixes_queued': fixes, 'builds_total': builds_total,
        'failed_tests': [t['test'] for t in runner.results['failed']]
    }, severity='INFO' if total_fail == 0 else 'WARNING')

    if total_fail == 0:
        log.info('[E2E-TEST] *** ALL TESTS PASS -- ZO-SENTINEL FULLY OPERATIONAL ***')

    return {'status': status, 'passed': total_pass, 'failed': total_fail, 'fixes_queued': fixes}


# ---------------------------------------------------------------------------
# Hook 3: on_build_success
# ---------------------------------------------------------------------------

_build_counter = 0
N_BUILD_INTERVAL = 20

def on_build_success(task, output_file):
    global _build_counter
    _build_counter += 1
    if _build_counter % N_BUILD_INTERVAL != 0: return None
    log.info('[INTERVAL-TEST] %d builds -- spot smoke on 10 newest files', _build_counter)
    py_files = sorted(PROJECT_DIR.glob('*.py'), key=lambda f: f.stat().st_mtime, reverse=True)[:10]
    fails = []
    for fpath in py_files:
        ok, reason = _smoke_file(fpath)
        if not ok:
            fails.append(fpath.name)
            _queue_smoke_fix(fpath.name, reason)
    if not fails: log.info('[INTERVAL-TEST] PASS (%d files)', len(py_files))
    else: log.warning('[INTERVAL-TEST] %d failures', len(fails))
    return {'builds': _build_counter, 'spot_fails': len(fails)}


# ---------------------------------------------------------------------------
# Hook 4: on_rescue_success
# ---------------------------------------------------------------------------

BEHAVIORAL_TESTS = {
    'search_api.py':      [('.split(', 'multi-term split'), ('/health', 'health route')],
    'registry_api.py':   [('/health', 'health route'), ('/v1/registry', 'registry route')],
    'approval_workflow.py': [('/health', 'health'), ('BaseModel', 'pydantic validation')],
    'schema.py':          [('CREATE TABLE', 'table creation'), ('create_all', 'create_all fn')],
}

def on_rescue_success(task, output_file):
    fname = Path(output_file).name
    checks = BEHAVIORAL_TESTS.get(fname)
    if not checks: return None
    fpath = PROJECT_DIR / fname
    if not fpath.exists(): return None
    content = fpath.read_text()
    fails = [(desc, needle) for needle, desc in checks if needle not in content]
    if not fails:
        log.info('[RESCUE-TEST] PASS %s', fname)
    else:
        reason = f'missing: {[d for d,_ in fails]}'
        log.warning('[RESCUE-TEST] FAIL %s -- %s', fname, reason)
        _queue_smoke_fix(fname, reason)
        _emit_event('rescue_behavioral_fail', {'file': fname, 'failures': fails}, 'WARNING')
    return {'file': fname, 'contract_fails': len(fails)}


# ---------------------------------------------------------------------------
# Fix directive queuing
# ---------------------------------------------------------------------------

FIX_DESCRIPTIONS = {
    'search_api.py': (
        'Fix search_api.py /search endpoint multi-word query. '
        'Split q by whitespace: terms=[t for t in q.split() if t]. '
        'For each term: conditions.append("(name ILIKE ? OR description ILIKE ?)"); '
        'params.extend([f"%{term}%", f"%{term}%"]). '
        'Preserve all other endpoints. FastAPI port 8782. run()+uvicorn. Heartbeat.'
    ),
    'approval_workflow.py': (
        'Fix approval_workflow.py POST /api/submit: add Pydantic BaseModel validation. '
        'class SubmitRequest(BaseModel): mcp_name:str, url:str, description:str, requested_by:str. '
        'FastAPI returns 422 automatically for invalid input. Keep all other endpoints.'
    ),
    'registry_api.py': (
        'Fix registry_api.py GET /v1/assess?mcp={name}: '
        'return {verdict, trust_score, server_id, name, confidence} or 404. '
        'Query: WHERE name ILIKE "%{mcp}%" OR server_id=mcp. Keep other endpoints.'
    ),
}


def _queue_smoke_fix(fname: str, reason: str) -> bool:
    if fname in _load_paused():
        log.warning('[hooks] %s is PAUSED -- skipping fix queue', fname)
        return False
    attempts = _load_fix_attempts()
    task_name = f'fix_{fname.replace(".py","").replace(".","_")}_smoke_fail'
    attempt_count = attempts.get(fname, 0) + 1
    if attempt_count > MAX_RETRIES:
        _pause_file(fname, f'Failed {attempt_count} fix attempts. Last: {reason[:100]}')
        _remove_queued_fix(task_name)
        return False
    attempts[fname] = attempt_count
    _save_fix_attempts(attempts)
    log.info('[hooks] %s fix attempt %d/%d', fname, attempt_count, MAX_RETRIES)
    DIRECTIVE_DIR.mkdir(exist_ok=True)
    for fp in glob.glob(str(DIRECTIVE_DIR / '*.json')):
        try:
            if json.loads(Path(fp).read_text()).get('task') == task_name:
                log.info('[hooks] fix already queued: %s', task_name)
                return False
        except Exception:
            pass
    clean_reason = strip_traceback(reason)
    description = FIX_DESCRIPTIONS.get(fname,
        f'Rebuild {fname}. Failure ({attempt_count}/{MAX_RETRIES}): {clean_reason}. '
        f'write_service:8772. No duckdb. run()+__main__. Heartbeat.')
    if attempt_count > 1:
        description += f' [Attempt {attempt_count}: {clean_reason}]'
    nums = []
    for fp in glob.glob(str(DIRECTIVE_DIR / '*.json')):
        try: nums.append(int(Path(fp).name.split('_')[0]))
        except ValueError: pass
    seq = max(nums, default=106) + 1
    fpath = DIRECTIVE_DIR / (str(seq).zfill(3) + '_' + task_name + '.json')
    payload = {
        'task': task_name, 'handler': 'generate_file',
        'output_file': fname, 'complexity': 'high', 'phase': '?',
        'priority': 0.97, 'description': description,
        'from': 'builder_test_hooks', 'fix_attempt': attempt_count, 'max_retries': MAX_RETRIES
    }
    try:
        fpath.write_text(json.dumps(payload, indent=2))
        log.warning('[hooks] Fix queued: %s (attempt %d/%d)', fpath.name, attempt_count, MAX_RETRIES)
        return True
    except Exception as e:
        log.error('[hooks] Failed to write fix directive: %s', e)
        return False


def _remove_queued_fix(task_name: str):
    removed = 0
    for fp in glob.glob(str(DIRECTIVE_DIR / '*.json')):
        try:
            if json.loads(Path(fp).read_text()).get('task') == task_name:
                Path(fp).rename(fp.replace('.json', '.paused.json'))
                removed += 1
        except Exception:
            pass
    if removed:
        log.warning('[hooks] Removed %d oscillating directive(s) for %s', removed, task_name)


def reset_fix_attempts(fname: str = None):
    attempts = _load_fix_attempts()
    if fname:
        attempts.pop(fname, None)
        log.info('[hooks] Reset fix counter for %s', fname)
    else:
        attempts.clear()
        log.info('[hooks] Reset all fix counters')
    _save_fix_attempts(attempts)
    if fname:
        paused = _load_paused()
        if fname in paused:
            paused.discard(fname)
            PAUSED_FILES_PATH.write_text(json.dumps({'paused': sorted(paused)}, indent=2))
            log.info('[hooks] %s removed from paused list', fname)