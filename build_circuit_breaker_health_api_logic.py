import logging
import requests
from datetime import datetime, timezone
from typing import Dict, List, Optional, Any

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(name)s] %(levelname)s: %(message)s',
    filename='/home/workspace/logs/circuit_breaker_health_api_logic.log'
)
logger = logging.getLogger(__name__)

SERVICE_NAME = 'circuit_breaker_health_api_logic'
PORT = 8790
WRITE_SERVICE_URL = 'http://localhost:8772'
QUERY_URL = 'http://localhost:8772/query'
EXECUTE_URL = 'http://localhost:8772/execute'

CIRCUIT_BREAKER_TABLE = 'circuit_breaker_states'
CIRCUIT_BREAKER_EVENTS_TABLE = 'circuit_breaker_events'
SERVICE_HEALTH_TABLE = 'service_health'

CIRCUIT_STATES = {
    'CLOSED': 'closed',
    'OPEN': 'open',
    'HALF_OPEN': 'half_open'
}

FAILURE_THRESHOLD = 5
RECOVERY_TIMEOUT_SECONDS = 30
HALF_OPEN_MAX_REQUESTS = 3


def ws_query(sql: str, params: Optional[List[Any]] = None) -> List[Dict[str, Any]]:
    payload = {'sql': sql}
    if params:
        payload['params'] = params
    try:
        response = requests.post(QUERY_URL, json=payload, timeout=30)
        response.raise_for_status()
        data = response.json()
        return data.get('rows', [])
    except requests.exceptions.RequestException as e:
        logger.error(f'Query failed: {e}')
        return []


def ws_write(table: str, rows: List[Dict[str, Any]]) -> bool:
    payload = {'table': table, 'rows': rows, 'wait': True}
    try:
        response = requests.post(WRITE_SERVICE_URL, json=payload, timeout=30)
        response.raise_for_status()
        return True
    except requests.exceptions.RequestException as e:
        logger.error(f'Write failed for table {table}: {e}')
        return False


def ws_execute(sql: str, params: Optional[List[Any]] = None) -> bool:
    payload = {'sql': sql}
    if params:
        payload['params'] = params
    try:
        response = requests.post(EXECUTE_URL, json=payload, timeout=30)
        response.raise_for_status()
        return True
    except requests.exceptions.RequestException as e:
        logger.error(f'Execute failed: {e}')
        return False


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def ensure_circuit_breaker_tables() -> bool:
    create_states_table = f'''
    CREATE TABLE IF NOT EXISTS {CIRCUIT_BREAKER_TABLE} (
        circuit_name VARCHAR PRIMARY KEY,
        state VARCHAR NOT NULL DEFAULT 'closed',
        failure_count INTEGER NOT NULL DEFAULT 0,
        last_failure_at TIMESTAMPTZ,
        last_success_at TIMESTAMPTZ,
        opened_at TIMESTAMPTZ,
        half_open_success_count INTEGER NOT NULL DEFAULT 0,
        updated_at TIMESTAMPTZ NOT NULL
    )
    '''
    
    create_events_table = f'''
    CREATE TABLE IF NOT EXISTS {CIRCUIT_BREAKER_EVENTS_TABLE} (
        event_id VARCHAR PRIMARY KEY,
        circuit_name VARCHAR NOT NULL,
        event_type VARCHAR NOT NULL,
        from_state VARCHAR,
        to_state VARCHAR,
        failure_count INTEGER,
        metadata JSON,
        created_at TIMESTAMPTZ NOT NULL
    )
    '''
    
    success = ws_execute(create_states_table)
    if success:
        success = ws_execute(create_events_table)
    
    return success


def get_circuit_state(circuit_name: str) -> Optional[Dict[str, Any]]:
    sql = f'SELECT * FROM {CIRCUIT_BREAKER_TABLE} WHERE circuit_name = ?'
    rows = ws_query(sql, [circuit_name])
    return rows[0] if rows else None


def get_all_circuit_states() -> List[Dict[str, Any]]:
    sql = f'SELECT * FROM {CIRCUIT_BREAKER_TABLE} ORDER BY circuit_name'
    return ws_query(sql)


def get_circuit_events(circuit_name: str, limit: int = 100) -> List[Dict[str, Any]]:
    sql = f'''
    SELECT * FROM {CIRCUIT_BREAKER_EVENTS_TABLE} 
    WHERE circuit_name = ? 
    ORDER BY created_at DESC 
    LIMIT ?
    '''
    return ws_query(sql, [circuit_name, limit])


def get_recent_failure_events(minutes: int = 60) -> List[Dict[str, Any]]:
    cutoff_time = datetime.now(timezone.utc).timestamp() - (minutes * 60)
    sql = f'''
    SELECT * FROM {CIRCUIT_BREAKER_EVENTS_TABLE} 
    WHERE event_type = 'trip' 
    AND created_at > to_timestamp(?)
    ORDER BY created_at DESC
    '''
    return ws_query(sql, [cutoff_time])


def compute_circuit_health(circuit_name: str) -> Dict[str, Any]:
    state = get_circuit_state(circuit_name)
    if not state:
        return {
            'circuit_name': circuit_name,
            'exists': False,
            'state': 'unknown',
            'health_score': 0.0,
            'is_healthy': False
        }
    
    now = datetime.now(timezone.utc)
    failure_count = state.get('failure_count', 0)
    last_failure = state.get('last_failure_at')
    last_success = state.get('last_success_at')
    
    base_health = 1.0
    if failure_count > 0:
        failure_penalty = min(failure_count / FAILURE_THRESHOLD, 1.0) * 0.5
        base_health -= failure_penalty
    
    if state['state'] == 'open':
        base_health *= 0.2
    elif state['state'] == 'half_open':
        base_health *= 0.6
    
    time_since_failure = None
    if last_failure:
        if isinstance(last_failure, str):
            last_failure_dt = datetime.fromisoformat(last_failure.replace('Z', '+00:00'))
        else:
            last_failure_dt = last_failure
        time_since_failure = (now - last_failure_dt).total_seconds()
    
    if time_since_failure and time_since_failure > RECOVERY_TIMEOUT_SECONDS and state['state'] == 'open':
        base_health = max(base_health, 0.4)
    
    health_score = max(0.0, min(1.0, base_health))
    
    return {
        'circuit_name': circuit_name,
        'exists': True,
        'state': state['state'],
        'failure_count': failure_count,
        'health_score': round(health_score, 4),
        'is_healthy': health_score >= 0.7 and state['state'] == 'closed',
        'last_failure_at': last_failure,
        'last_success_at': last_success,
        'time_since_failure': time_since_failure,
        'should_attempt_recovery': (
            state['state'] == 'open' and 
            time_since_failure is not None and 
            time_since_failure >= RECOVERY_TIMEOUT_SECONDS
        )
    }


def compute_all_circuits_health() -> List[Dict[str, Any]]:
    circuits = get_all_circuit_states()
    return [compute_circuit_health(c['circuit_name']) for c in circuits]


def record_circuit_event(
    circuit_name: str,
    event_type: str,
    from_state: Optional[str] = None,
    to_state: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None
) -> bool:
    import hashlib
    
    event_id_input = f'{circuit_name}:{event_type}:{utc_now_iso()}'
    event_id = hashlib.sha256(event_id_input.encode()).hexdigest()[:16]
    
    event_row = {
        'event_id': event_id,
        'circuit_name': circuit_name,
        'event_type': event_type,
        'from_state': from_state,
        'to_state': to_state,
        'metadata': metadata or {},
        'created_at': utc_now_iso()
    }
    
    return ws_write(CIRCUIT_BREAKER_EVENTS_TABLE, [event_row])


def trip_circuit(circuit_name: str, reason: str = '') -> bool:
    state = get_circuit_state(circuit_name)
    from_state = state['state'] if state else 'unknown'
    
    now = utc_now_iso()
    new_failure_count = (state['failure_count'] + 1) if state else 1
    
    if new_failure_count >= FAILURE_THRESHOLD:
        state_data = {
            'circuit_name': circuit_name,
            'state': CIRCUIT_STATES['OPEN'],
            'failure_count': new_failure_count,
            'last_failure_at': now,
            'opened_at': now,
            'half_open_success_count': 0,
            'updated_at': now
        }
    else:
        state_data = {
            'circuit_name': circuit_name,
            'failure_count': new_failure_count,
            'last_failure_at': now,
            'updated_at': now
        }
    
    success = ws_write(CIRCUIT_BREAKER_TABLE, [state_data])
    if success:
        record_circuit_event(
            circuit_name=circuit_name,
            event_type='trip',
            from_state=from_state,
            to_state=CIRCUIT_STATES['OPEN'] if new_failure_count >= FAILURE_THRESHOLD else from_state,
            metadata={'reason': reason, 'failure_count': new_failure_count}
        )
    
    return success


def reset_circuit(circuit_name: str) -> bool:
    state = get_circuit_state(circuit_name)
    from_state = state['state'] if state else 'unknown'
    
    if not state:
        state_data = {
            'circuit_name': circuit_name,
            'state': CIRCUIT_STATES['CLOSED'],
            'failure_count': 0,
            'last_success_at': utc_now_iso(),
            'opened_at': None,
            'half_open_success_count': 0,
            'updated_at': utc_now_iso()
        }
    else:
        state_data = {
            'circuit_name': circuit_name,
            'state': CIRCUIT_STATES['CLOSED'],
            'failure_count': 0,
            'last_success_at': utc_now_iso(),
            'opened_at': None,
            'half_open_success_count': 0,
            'updated_at': utc_now_iso()
        }
    
    success = ws_write(CIRCUIT_BREAKER_TABLE, [state_data])
    if success:
        record_circuit_event(
            circuit_name=circuit_name,
            event_type='reset',
            from_state=from_state,
            to_state=CIRCUIT_STATES['CLOSED'],
            metadata={'triggered_by': 'recovery_timeout'}
        )
    
    return success


def record_success(circuit_name: str) -> bool:
    state = get_circuit_state(circuit_name)
    now = utc_now_iso()
    
    if not state:
        state_data = {
            'circuit_name': circuit_name,
            'state': CIRCUIT_STATES['CLOSED'],
            'failure_count': 0,
            'last_success_at': now,
            'half_open_success_count': 0,
            'updated_at': now
        }
    elif state['state'] == CIRCUIT_STATES['HALF_OPEN']:
        half_open_success_count = state.get('half_open_success_count', 0) + 1
        
        if half_open_success_count >= HALF_OPEN_MAX_REQUESTS:
            state_data = {
                'circuit_name': circuit_name,
                'state': CIRCUIT_STATES['CLOSED'],
                'failure_count': 0,
                'last_success_at': now,
                'half_open_success_count': 0,
                'updated_at': now
            }
            record_circuit_event(
                circuit_name=circuit_name,
                event_type='recovery',
                from_state=CIRCUIT_STATES['HALF_OPEN'],
                to_state=CIRCUIT_STATES['CLOSED'],
                metadata={'half_open_attempts': half_open_success_count}
            )
        else:
            state_data = {
                'circuit_name': circuit_name,
                'half_open_success_count': half_open_success_count,
                'last_success_at': now,
                'updated_at': now
            }
    else:
        state_data = {
            'circuit_name': circuit_name,
            'failure_count': max(0, state.get('failure_count', 0) - 1),
            'last_success_at': now,
            'updated_at': now
        }
    
    return ws_write(CIRCUIT_BREAKER_TABLE, [state_data])


def attempt_half_open(circuit_name: str) -> bool:
    state = get_circuit_state(circuit_name)
    
    if not state or state['state'] != CIRCUIT_STATES['OPEN']:
        return False
    
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)
    last_failure = state.get('last_failure_at') or state.get('opened_at')
    
    if not last_failure:
        return False
    
    if isinstance(last_failure, str):
        last_failure_dt = datetime.fromisoformat(last_failure.replace('Z', '+00:00'))
    else:
        last_failure_dt = last_failure
    
    time_since_failure = (now - last_failure_dt).total_seconds()
    
    if time_since_failure < RECOVERY_TIMEOUT_SECONDS:
        return False
    
    state_data = {
        'circuit_name': circuit_name,
        'state': CIRCUIT_STATES['HALF_OPEN'],
        'half_open_success_count': 0,
        'updated_at': utc_now_iso()
    }
    
    success = ws_write(CIRCUIT_BREAKER_TABLE, [state_data])
    if success:
        record_circuit_event(
            circuit_name=circuit_name,
            event_type='half_open',
            from_state=CIRCUIT_STATES['OPEN'],
            to_state=CIRCUIT_STATES['HALF_OPEN'],
            metadata={'recovery_timeout_reached': True}
        )
    
    return success


def is_circuit_allowing_requests(circuit_name: str) -> bool:
    health = compute_circuit_health(circuit_name)
    
    if not health['exists']:
        return True
    
    if health['state'] == CIRCUIT_STATES['CLOSED']:
        return True
    elif health['state'] == CIRCUIT_STATES['HALF_OPEN']:
        return True
    else:
        return False


def get_circuit_breaker_summary() -> Dict[str, Any]:
    all_health = compute_all_circuits_health()
    
    total = len(all_health)
    healthy = sum(1 for h in all_health if h.get('is_healthy', False))
    open_circuits = sum(1 for h in all_health if h.get('state') == 'open')
    half_open_circuits = sum(1 for h in all_health if h.get('state') == 'half_open')
    
    avg_health = sum(h.get('health_score', 0) for h in all_health) / total if total > 0 else 0.0
    
    return {
        'total_circuits': total,
        'healthy_circuits': healthy,
        'unhealthy_circuits': total - healthy,
        'open_circuits': open_circuits,
        'half_open_circuits': half_open_circuits,
        'average_health_score': round(avg_health, 4),
        'overall_healthy': healthy == total and open_circuits == 0,
        'circuits': all_health,
        'timestamp': utc_now_iso()
    }


def get_circuit_recommendations() -> List[Dict[str, Any]]:
    recommendations = []
    all_health = compute_all_circuits_health()
    
    for health in all_health:
        if not health.get('exists'):
            continue
        
        circuit_name = health['circuit_name']
        
        if health['state'] == 'open' and health.get('should_attempt_recovery'):
            recommendations.append({
                'circuit_name': circuit_name,
                'action': 'attempt_half_open',
                'priority': 'high',
                'reason': 'Recovery timeout reached, circuit should attempt half-open state',
                'metadata': {
                    'time_since_failure': health.get('time_since_failure'),
                    'recovery_timeout': RECOVERY_TIMEOUT_SECONDS
                }
            })
        
        if health['failure_count'] >= FAILURE_THRESHOLD - 1 and health['state'] == 'closed':
            recommendations.append({
                'circuit_name': circuit_name,
                'action': 'monitor',
                'priority': 'medium',
                'reason': f'Failure count is {health["failure_count"]}, approaching threshold',
                'metadata': {
                    'failure_count': health['failure_count'],
                    'threshold': FAILURE_THRESHOLD
                }
            })
        
        if health['state'] == 'half_open' and health['failure_count'] > 0:
            recommendations.append({
                'circuit_name': circuit_name,
                'action': 'monitor',
                'priority': 'medium',
                'reason': 'Circuit in half-open state with prior failures',
                'metadata': {
                    'failure_count': health['failure_count']
                }
            })
    
    return recommendations


def send_heartbeat() -> bool:
    health = get_circuit_breaker_summary()
    heartbeat_row = {
        'service': SERVICE_NAME,
        'status': 'ok' if health['overall_healthy'] else 'degraded',
        'last_heartbeat': utc_now_iso(),
        'meta': {
            'total_circuits': health['total_circuits'],
            'healthy_circuits': health['healthy_circuits'],
            'open_circuits': health['open_circuits'],
            'average_health_score': health['average_health_score']
        }
    }
    return ws_write(SERVICE_HEALTH_TABLE, [heartbeat_row])


def run():
    logger.info(f'{SERVICE_NAME} starting...')
    
    ensure_circuit_breaker_tables()
    
    send_heartbeat()
    
    logger.info(f'{SERVICE_NAME} initialized successfully')
    logger.info(f'Circuit breaker health API ready on port {PORT}')


if __name__ == '__main__':
    run()