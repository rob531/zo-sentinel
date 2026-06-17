import logging
import os
import sys
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import requests

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(name)s] %(levelname)s: %(message)s',
    handlers=[
        logging.FileHandler('/home/workspace/logs/verify_snow_connector_inbound_wiring.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger('verify_snow_connector_inbound_wiring')

SERVICE_NAME = 'verify_snow_connector_inbound_wiring'
WRITE_SERVICE_URL = 'http://localhost:8772'
QUERY_SERVICE_URL = 'http://localhost:8772/query'
EXECUTE_SERVICE_URL = 'http://localhost:8772/execute'
SNOW_INBOUND_WEBHOOK_PORT = 8788
SNOW_CONNECTOR_PORT = 8786


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')


def ws_query(sql: str, params: Optional[List[Any]] = None) -> List[Dict[str, Any]]:
    payload: Dict[str, Any] = {'sql': sql}
    if params:
        payload['params'] = params
    try:
        resp = requests.post(QUERY_SERVICE_URL, json=payload, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        return data.get('rows', [])
    except requests.exceptions.RequestException as e:
        logger.error(f"ws_query failed: {e}")
        return []


def ws_write(table: str, rows: List[Dict[str, Any]]) -> bool:
    payload = {'table': table, 'rows': rows}
    try:
        resp = requests.post(WRITE_SERVICE_URL, json=payload, timeout=30)
        resp.raise_for_status()
        return True
    except requests.exceptions.RequestException as e:
        logger.error(f"ws_write failed for table {table}: {e}")
        return False


def ws_execute(sql: str, params: Optional[List[Any]] = None) -> bool:
    payload: Dict[str, Any] = {'sql': sql}
    if params:
        payload['params'] = params
    try:
        resp = requests.post(EXECUTE_SERVICE_URL, json=payload, timeout=30)
        resp.raise_for_status()
        return True
    except requests.exceptions.RequestException as e:
        logger.error(f"ws_execute failed: {e}")
        return False


def check_service_health(port: int, service_label: str) -> Dict[str, Any]:
    result = {
        'service': service_label,
        'port': port,
        'reachable': False,
        'status': 'unknown',
        'response_time_ms': 0
    }
    health_url = f'http://localhost:{port}/health'
    try:
        start = datetime.now(timezone.utc)
        resp = requests.get(health_url, timeout=5)
        elapsed_ms = (datetime.now(timezone.utc) - start).total_seconds() * 1000
        result['response_time_ms'] = elapsed_ms
        result['reachable'] = True
        if resp.status_code == 200:
            result['status'] = 'healthy'
            try:
                result['details'] = resp.json()
            except Exception:
                result['details'] = resp.text[:200]
        else:
            result['status'] = f'unhealthy_http_{resp.status_code}'
    except requests.exceptions.Timeout:
        result['status'] = 'timeout'
    except requests.exceptions.RequestException as e:
        result['status'] = f'error_{type(e).__name__}'
    return result


def check_snow_inbound_webhook_endpoint() -> Dict[str, Any]:
    result = {
        'endpoint': '/webhook/snow/inbound',
        'methods': [],
        'content_types': [],
        'authenticated': False
    }
    openapi_url = f'http://localhost:{SNOW_INBOUND_WEBHOOK_PORT}/openapi.json'
    try:
        resp = requests.get(openapi_url, timeout=10)
        if resp.status_code == 200:
            spec = resp.json()
            paths = spec.get('paths', {})
            for path, methods in paths.items():
                if 'webhook' in path.lower() or 'snow' in path.lower():
                    result['methods'].extend([m.upper() for m in methods.keys() if m != 'parameters'])
            if '/webhook/snow/inbound' in paths:
                post_op = paths['/webhook/snow/inbound'].get('post', {})
                result['authenticated'] = 'security' in post_op or 'security' in paths['/webhook/snow/inbound']
                result['content_types'] = [
                    list(cont.keys())[0] if isinstance(cont, dict) else str(cont)
                    for cont in post_op.get('requestBody', {}).get('content', {}).keys()
                ]
    except requests.exceptions.RequestException:
        pass
    return result


def check_snow_connector_inbound_wiring() -> Dict[str, Any]:
    result = {
        'snow_connector_has_inbound_handler': False,
        'inbound_webhook_table_exists': False,
        'ticket_processing_function': False,
        'verdict_lookup_wired': False,
        'audit_trail_wired': False
    }
    
    sql_checks = [
        ("SELECT table_name FROM information_schema.tables WHERE table_name LIKE '%snow%inbound%' OR table_name LIKE '%snow%webhook%'",
         'inbound_webhook_table_exists'),
        ("SELECT COUNT(*) > 0 as has_func FROM information_schema.routines WHERE routine_name LIKE '%process%snow%inbound%' OR routine_name LIKE '%handle%snow%ticket%'",
         'ticket_processing_function'),
        ("SELECT COUNT(*) > 0 as wired FROM information_schema.table_constraints WHERE constraint_name LIKE '%snow%verdict%'",
         'verdict_lookup_wired'),
        ("SELECT COUNT(*) > 0 as wired FROM information_schema.tables WHERE table_name = 'audit_log'",
         'audit_trail_wired'),
    ]
    
    for sql, key in sql_checks:
        rows = ws_query(sql)
        if rows:
            first_row = rows[0]
            result[key] = any(val for val in first_row.values() if isinstance(val, bool))
    
    snow_connector_source = '/home/workspace/zo_sentinel/snow_connector.py'
    if os.path.exists(snow_connector_source):
        with open(snow_connector_source, 'r') as f:
            content = f.read()
            result['snow_connector_has_inbound_handler'] = (
                'inbound' in content.lower() and 
                ('webhook' in content.lower() or 'handle_snow_ticket' in content)
            )
    
    return result


def check_inbound_ticket_flow() -> Dict[str, Any]:
    result = {
        'pending_tickets_query': False,
        'ticket_hash_computation': False,
        'snow_webhook_route_exists': False,
        'verdict_gate_check': False
    }
    
    sql_checks = [
        ("SELECT COUNT(*) > 0 FROM information_schema.tables WHERE table_name LIKE '%snow%inbound%' OR table_name = 'snow_inbound_webhooks'",
         'pending_tickets_query'),
        ("SELECT COUNT(*) > 0 FROM information_schema.tables WHERE table_name LIKE '%snow%' AND (table_name LIKE '%hash%' OR table_name LIKE '%ticket%')",
         'ticket_hash_computation'),
    ]
    
    for sql, key in sql_checks:
        rows = ws_query(sql)
        if rows:
            result[key] = any(val for val in rows[0].values() if isinstance(val, bool))
    
    webhook_handler_path = '/home/workspace/zo_sentinel/snow_inbound_webhook.py'
    if os.path.exists(webhook_handler_path):
        with open(webhook_handler_path, 'r') as f:
            content = f.read()
            result['snow_webhook_route_exists'] = '@app.post' in content and 'webhook' in content.lower()
            result['verdict_gate_check'] = 'verdict' in content.lower() and ('check' in content.lower() or 'gate' in content.lower())
    
    return result


def check_service_heartbeat_wiring() -> Dict[str, Any]:
    result = {
        'snow_connector_heartbeat': False,
        'snow_inbound_heartbeat': False,
        'heartbeat_age_seconds': {}
    }
    
    services = ['snow_connector', 'snow_inbound_webhook']
    for svc in services:
        rows = ws_query(f"SELECT last_heartbeat FROM service_health WHERE service = '{svc}'")
        if rows and 'last_heartbeat' in rows[0]:
            ts_str = rows[0]['last_heartbeat']
            if ts_str:
                try:
                    ts = datetime.fromisoformat(ts_str.replace('Z', '+00:00'))
                    age = (datetime.now(timezone.utc) - ts).total_seconds()
                    result['heartbeat_age_seconds'][svc] = age
                    result[f'{svc.replace("-", "_").replace(" ", "_")}_heartbeat'] = age < 300
                except Exception:
                    result['heartbeat_age_seconds'][svc] = -1
            else:
                result['heartbeat_age_seconds'][svc] = -2
        else:
            result['heartbeat_age_seconds'][svc] = -3
    
    return result


def check_supervisord_registration() -> Dict[str, Any]:
    result = {
        'snow_connector_registered': False,
        'snow_inbound_registered': False,
        'config_files_found': []
    }
    
    supervisord_configs = [
        '/home/workspace/zo_sentinel/supervisord_sentinel_full.conf',
        '/etc/supervisor/conf.d/sentinel.conf'
    ]
    
    for config_path in supervisord_configs:
        if os.path.exists(config_path):
            result['config_files_found'].append(config_path)
            with open(config_path, 'r') as f:
                content = f.read()
                if 'snow_connector' in content:
                    result['snow_connector_registered'] = True
                if 'snow_inbound' in content or 'snow_webhook' in content:
                    result['snow_inbound_registered'] = True
    
    return result


def run_verification() -> Dict[str, Any]:
    verification = {
        'timestamp': utc_now_iso(),
        'overall_status': 'PASS',
        'checks': {}
    }
    
    logger.info("Starting Snow Connector inbound wiring verification...")
    
    verification['checks']['snow_connector_health'] = check_service_health(SNOW_CONNECTOR_PORT, 'snow_connector')
    verification['checks']['snow_inbound_health'] = check_service_health(SNOW_INBOUND_WEBHOOK_PORT, 'snow_inbound_webhook')
    
    verification['checks']['inbound_endpoint'] = check_snow_inbound_webhook_endpoint()
    
    verification['checks']['wiring'] = check_snow_connector_inbound_wiring()
    
    verification['checks']['ticket_flow'] = check_inbound_ticket_flow()
    
    verification['checks']['heartbeat_wiring'] = check_service_heartbeat_wiring()
    
    verification['checks']['supervisord'] = check_supervisord_registration()
    
    failed_checks = []
    for check_name, check_result in verification['checks'].items():
        if isinstance(check_result, dict):
            if 'reachable' in check_result and not check_result['reachable']:
                failed_checks.append(f"{check_name}_unreachable")
            if check_name == 'wiring':
                for key, val in check_result.items():
                    if isinstance(val, bool) and not val and key != 'audit_trail_wired':
                        failed_checks.append(f"wiring_{key}")
            if check_name == 'ticket_flow':
                for key, val in check_result.items():
                    if isinstance(val, bool) and not val:
                        failed_checks.append(f"ticket_flow_{key}")
    
    if failed_checks:
        verification['overall_status'] = 'FAIL'
        verification['failed_checks'] = failed_checks
        logger.warning(f"Verification FAILED. Failed checks: {failed_checks}")
    else:
        logger.info("Verification PASSED. All checks successful.")
    
    return verification


def write_verification_result(verification: Dict[str, Any]) -> bool:
    result_row = {
        'service': SERVICE_NAME,
        'check_timestamp': verification['timestamp'],
        'overall_status': verification['overall_status'],
        'snow_connector_reachable': verification['checks'].get('snow_connector_health', {}).get('reachable', False),
        'snow_inbound_reachable': verification['checks'].get('snow_inbound_health', {}).get('reachable', False),
        'wiring_complete': verification['checks'].get('wiring', {}).get('snow_connector_has_inbound_handler', False),
        'ticket_flow_complete': verification['checks'].get('ticket_flow', {}).get('snow_webhook_route_exists', False),
        'heartbeat_wired': verification['checks'].get('heartbeat_wiring', {}).get('snow_connector_heartbeat', False),
        'failed_checks': str(verification.get('failed_checks', [])),
        'meta': str(verification)
    }
    
    if not ws_write('service_health', [{
        'service': SERVICE_NAME,
        'last_heartbeat': utc_now_iso(),
        'status': verification['overall_status'].lower(),
        'ts': utc_now_iso(),
        'meta': str(result_row)
    }]):
        return False
    
    return True


def run() -> int:
    logger.info("=" * 60)
    logger.info(f"Starting {SERVICE_NAME}")
    logger.info("=" * 60)
    
    verification = run_verification()
    
    write_verification_result(verification)
    
    logger.info(f"Verification completed: {verification['overall_status']}")
    logger.info(f"Results: {verification}")
    
    if verification['overall_status'] == 'PASS':
        return 0
    else:
        return 1


if __name__ == '__main__':
    sys.exit(run())