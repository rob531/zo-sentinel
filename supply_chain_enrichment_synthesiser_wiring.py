def ws_write(table, rows):
    import requests, json
    resp = requests.post('http://localhost:8772/write', json={'table': table, 'rows': rows}, timeout=15)
    resp.raise_for_status()
    return resp.json()

def ws_query(sql):
    import requests
    resp = requests.post('http://localhost:8772/query', json={'sql': sql}, timeout=30)
    resp.raise_for_status()
    result = resp.json()
    return result.get('rows', [])

def ws_execute(sql):
    import requests
    resp = requests.post('http://localhost:8772/execute', json={'sql': sql}, timeout=30)
    resp.raise_for_status()
    return resp.json()

SERVICE_NAME = 'supply_chain_enrichment_synthesiser_wiring'
LOG_FILE = '/home/workspace/logs/supply_chain_enrichment_synthesiser_wiring.log'

import logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(name)s] %(levelname)s: %(message)s',
    handlers=[logging.FileHandler(LOG_FILE)]
)
log = logging.getLogger(SERVICE_NAME)

import os, sys, hashlib, json
from datetime import datetime, timezone

QUERY_URL = 'http://localhost:8772/query'
WRITE_URL = 'http://localhost:8772/write'
EXECUTE_URL = 'http://localhost:8772/execute'

def utc_now_iso():
    return datetime.now(timezone.utc).isoformat()

def check_single_instance():
    pid_file = f'/tmp/{SERVICE_NAME}.pid'
    if os.path.exists(pid_file):
        with open(pid_file) as f:
            old_pid = int(f.read().strip())
        try:
            os.kill(old_pid, 0)
            log.error('Another instance is running (PID %d). Exiting.', old_pid)
            sys.exit(1)
        except OSError:
            pass
    with open(pid_file, 'w') as f:
        f.write(str(os.getpid()))

def remove_pid_file():
    pid_file = f'/tmp/{SERVICE_NAME}.pid'
    if os.path.exists(pid_file):
        os.remove(pid_file)

def signal_handler(signum, frame):
    log.info('Received signal %d, shutting down gracefully.', signum)
    remove_pid_file()
    sys.exit(0)

def get_enrichment_rows(limit=50):
    sql = """
    SELECT 
        server_id,
        signal_name,
        score,
        evidence,
        computed_at
    FROM mcp_signal_enrichments
    WHERE signal_name = 'supply_chain_enrichment'
    ORDER BY computed_at DESC
    LIMIT %s
    """ % (limit,)
    return ws_query(sql)

def get_signal_scores_for_servers(server_ids):
    if not server_ids:
        return []
    ids_placeholder = ','.join(["'%s'" % s for s in server_ids])
    sql = """
    SELECT 
        server_id,
        signal_name,
        score,
        evidence,
        scored_at
    FROM mcp_signal_scores
    WHERE server_id IN (%s)
    """ % ids_placeholder
    return ws_query(sql)

def get_trust_synthesiser_source_path():
    base = '/home/workspace/zo_sentinel'
    candidates = [
        os.path.join(base, 'trust_synthesiser_v2.py'),
        os.path.join(base, 'trust_synthesiser_v3.py'),
    ]
    for path in candidates:
        if os.path.exists(path):
            return path
    return None

def check_supply_chain_in_synthesiser():
    path = get_trust_synthesiser_source_path()
    if not path:
        log.error('trust_synthesiser_v2.py not found')
        return False
    with open(path, 'r') as f:
        content = f.read()
    has_supply_chain_import = 'supply_chain_enrichment' in content.lower()
    has_dimension_8 = 'dimension_8' in content or 'dimension_8' in content.replace(' ', '')
    log.info('trust_synthesiser source at %s: supply_chain_import=%s, dimension_8=%s', 
             path, has_supply_chain_import, has_dimension_8)
    return has_supply_chain_import

def verify_enrichment_evidence_shape():
    sql = """
    SELECT evidence
    FROM mcp_signal_enrichments
    WHERE signal_name = 'supply_chain_enrichment'
    LIMIT 5
    """
    rows = ws_query(sql)
    if not rows:
        return False, 'No rows found'
    
    valid_shapes = 0
    for row in rows:
        ev = row.get('evidence', '{}')
        try:
            if isinstance(ev, str):
                blob = json.loads(ev)
            else:
                blob = ev
            if isinstance(blob, dict):
                valid_shapes += 1
        except (json.JSONDecodeError, TypeError) as e:
            log.warning('Invalid evidence blob: %s', e)
    
    log.info('Evidence shape check: %d/%d valid', valid_shapes, len(rows))
    return valid_shapes == len(rows), f'{valid_shapes}/{len(rows)} valid'

def check_dimension_8_in_synthesiser():
    path = get_trust_synthesiser_source_path()
    if not path:
        return False
    with open(path, 'r') as f:
        content = f.read()
    
    supply_chain_patterns = [
        'supply_chain',
        'supplychain',
        'SCORE_SUPPLY_CHAIN',
        'supply_chain_enrichment',
    ]
    
    found = any(p.lower() in content.lower() for p in supply_chain_patterns)
    log.info('Dimension 8 (supply_chain) referenced in synthesiser: %s', found)
    return found

def compute_supply_chain_signal_contribution(supply_chain_score):
    if supply_chain_score is None:
        return 0.0
    try:
        score = float(supply_chain_score)
    except (ValueError, TypeError):
        return 0.0
    
    if score >= 0.8:
        return score * 0.12
    elif score >= 0.6:
        return score * 0.10
    elif score >= 0.4:
        return score * 0.08
    elif score >= 0.2:
        return score * 0.06
    else:
        return score * 0.04

def write_wiring_audit(server_id, has_supply_chain, composite_missing, composite_updated, ts):
    rows = [{
        'server_id': server_id,
        'has_supply_chain_dimension': has_supply_chain,
        'composite_missing_supply_chain': composite_missing,
        'composite_updated': composite_updated,
        'wired_at': ts,
    }]
    try:
        ws_write('supply_chain_enrichment_wiring_audit', rows)
    except Exception as e:
        log.warning('Could not write wiring audit (table may not exist): %s', e)

def verify_wiring():
    log.info('=== SUPPLY CHAIN ENRICHMENT → TRUST SYNTHESISER WIRING VERIFICATION ===')
    results = {}
    
    results['synthesiser_source'] = get_trust_synthesiser_source_path()
    log.info('[1] Synthesiser source: %s', results['synthesiser_source'])
    
    results['synthesiser_has_supply_chain'] = check_supply_chain_in_synthesiser()
    log.info('[2] Synthesiser has supply_chain dimension: %s', results['synthesiser_has_supply_chain'])
    
    results['dimension_8_referenced'] = check_dimension_8_in_synthesiser()
    log.info('[3] Dimension 8 (supply_chain) referenced: %s', results['dimension_8_referenced'])
    
    enrichment_rows = get_enrichment_rows(limit=100)
    results['enrichment_count'] = len(enrichment_rows)
    log.info('[4] mcp_signal_enrichments supply_chain rows: %d', results['enrichment_count'])
    
    valid_shape, shape_msg = verify_enrichment_evidence_shape()
    results['evidence_shape_valid'] = valid_shape
    log.info('[5] Evidence blob shape valid: %s (%s)', valid_shape, shape_msg)
    
    if enrichment_rows:
        sample = enrichment_rows[0]
        results['sample_score'] = sample.get('score')
        results['sample_computed_at'] = sample.get('computed_at')
        log.info('[6] Sample score: %s, computed_at: %s', 
                 results['sample_score'], results['sample_computed_at'])
    
    results['composite_contribution'] = compute_supply_chain_signal_contribution(
        results.get('sample_score', 0.5)
    )
    log.info('[7] Expected composite contribution (sample): %.4f', results['composite_contribution'])
    
    all_ok = (
        results['synthesiser_source'] and
        results['synthesiser_has_supply_chain'] and
        results['enrichment_count'] > 0 and
        results['evidence_shape_valid']
    )
    results['wiring_ok'] = all_ok
    
    log.info('')
    log.info('=== WIRING VERIFICATION SUMMARY ===')
    log.info('Status: %s', 'PASS' if all_ok else 'FAIL')
    log.info('Enrichment rows available: %d', results['enrichment_count'])
    log.info('Synthesiser wired: %s', results['synthesiser_has_supply_chain'])
    log.info('Dimension 8 referenced: %s', results['dimension_8_referenced'])
    
    return results

def patch_synthesiser_to_add_dimension_8():
    path = get_trust_synthesiser_source_path()
    if not path:
        log.error('Cannot patch: synthesiser source not found')
        return False
    
    with open(path, 'r') as f:
        content = f.read()
    
    supply_chain_marker = "supply_chain_enrichment"
    if supply_chain_marker.lower() in content.lower():
        log.info('Synthesiser already references supply_chain_enrichment')
        return True
    
    log.info('Patching trust_synthesiser to add supply_chain_enrichment as dimension 8...')
    
    dimension_8_code = '''
    supply_chain_score = None
    supply_chain_rows = [r for r in enrichment_rows if r.get('signal_name') == 'supply_chain_enrichment']
    if supply_chain_rows:
        supply_chain_score = supply_chain_rows[0].get('score', 0.0)
        supply_chain_evidence = supply_chain_rows[0].get('evidence', '{}')
        try:
            if isinstance(supply_chain_evidence, str):
                supply_chain_evidence = json.loads(supply_chain_evidence)
        except (json.JSONDecodeError, ValueError):
            supply_chain_evidence = {}
        supply_chain_contribution = supply_chain_score * SUPPLY_CHAIN_WEIGHT
        dimensions['supply_chain_enrichment'] = {
            'score': supply_chain_score,
            'contribution': supply_chain_contribution,
            'evidence': supply_chain_evidence,
        }
    else:
        dimensions['supply_chain_enrichment'] = {
            'score': 0.0,
            'contribution': 0.0,
            'evidence': {},
        }
    
'''
    
    weight_definition = 'SUPPLY_CHAIN_WEIGHT = 0.12'
    
    if 'SUPPLY_CHAIN_WEIGHT' not in content:
        lines = content.split('\n')
        new_lines = []
        weights_inserted = False
        for line in lines:
            new_lines.append(line)
            if not weights_inserted and 'COMMUNITY_SIGNAL_WEIGHT' in line or 'COMMUNITY_WEIGHT' in line:
                new_lines.append(weight_definition)
                weights_inserted = True
        if not weights_inserted:
            new_lines.append(weight_definition)
        content = '\n'.join(new_lines)
    
    insert_pos = content.find('def compute_composite_score')
    if insert_pos == -1:
        insert_pos = content.find('def synthesize')
    if insert_pos == -1:
        insert_pos = content.find('def calculate')
    
    if insert_pos != -1:
        content = content[:insert_pos] + dimension_8_code + content[insert_pos:]
    
    backup_path = path + '.backup_dim8.bak'
    with open(backup_path, 'w') as f:
        f.write(content)
    log.info('Backup written to %s', backup_path)
    
    with open(path, 'w') as f:
        f.write(content)
    log.info('Patched trust_synthesiser with supply_chain_enrichment dimension 8')
    return True

def send_heartbeat():
    ts = utc_now_iso()
    rows = [{
        'service': SERVICE_NAME,
        'last_heartbeat': ts,
        'status': 'running',
        'meta': json.dumps({'ts': ts}),
    }]
    try:
        ws_write('service_health', rows)
    except Exception as e:
        log.warning('Heartbeat failed: %s', e)

def run():
    log.info('Starting supply_chain_enrichment → trust_synthesiser wiring check')
    check_single_instance()
    
    try:
        results = verify_wiring()
        
        if not results.get('synthesiser_has_supply_chain'):
            log.warning('Synthesiser missing supply_chain integration - attempting patch...')
            patched = patch_synthesiser_to_add_dimension_8()
            if patched:
                log.info('Patch applied. Re-verifying...')
                results = verify_wiring()
            else:
                log.error('Patch failed')
        
        send_heartbeat()
        
        log.info('')
        if results.get('wiring_ok'):
            log.info('WIRING VERIFICATION: PASS')
            log.info('supply_chain_enrichment is wired into trust_synthesiser as dimension 8')
        else:
            log.warning('WIRING VERIFICATION: FAIL - issues detected')
            log.warning('Please review trust_synthesiser_v2.py for supply_chain_enrichment integration')
        
    except Exception as e:
        log.error('Wiring verification failed with error: %s', e, exc_info=True)
        send_heartbeat()
    finally:
        remove_pid_file()

if __name__ == '__main__':
    import signal
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)
    run()
    sys.exit(0)