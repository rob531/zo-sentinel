import requests
from datetime import datetime, timedelta

def run_diagnostic():
    # Initialize the result dictionary
    result = {
        'status': None,
        'mcp_count': 0,
        'populator_health_status': None,
        'recent_errors': [],
        'diagnosis_message': ''
    }

    # Query the mcp_server_registry table
    mcp_response = requests.get('http://write_service/mcp_server_registry')
    if mcp_response.status_code == 200:
        mcp_data = mcp_response.json()
        result['mcp_count'] = len(mcp_data)
        if result['mcp_count'] == 0:
            result['status'] = 'empty_table'
            result['diagnosis_message'] = 'No MCPs found in the mcp_server_registry table.'
            return result

    # Query the service_health table for the populator
    populator_response = requests.get('http://write_service/service_health?service_name=mcp_definition_history_populator')
    if populator_response.status_code == 200:
        populator_data = populator_response.json()
        if populator_data:
            result['populator_health_status'] = populator_data[0]['status']
            last_heartbeat = datetime.strptime(populator_data[0]['last_heartbeat'], '%Y-%m-%d %H:%M:%S')
            if last_heartbeat < datetime.now() - timedelta(minutes=5):
                result['status'] = 'populator_failed'
                result['diagnosis_message'] = 'The mcp_definition_history_populator has not sent a heartbeat in the last 5 minutes.'
                return result
        else:
            result['status'] = 'populator_failed'
            result['diagnosis_message'] = 'No health status found for the mcp_definition_history_populator.'
            return result

    # Query the audit_log table for recent errors
    audit_response = requests.get('http://write_service/audit_log?target_server_id=mcp_definition_history_populator&timestamp=' + (datetime.now() - timedelta(hours=1)).strftime('%Y-%m-%d %H:%M:%S'))
    if audit_response.status_code == 200:
        audit_data = audit_response.json()
        for entry in audit_data:
            if entry['level'] == 'ERROR':
                result['recent_errors'].append(entry)

    if result['recent_errors']:
        result['status'] = 'populator_failed'
        result['diagnosis_message'] = 'Recent errors found in the audit_log for the mcp_definition_history_populator.'
    else:
        result['status'] = 'populator_healthy'
        result['diagnosis_message'] = 'The mcp_definition_history_populator is healthy and no recent errors were found.'

    return result

if __name__ == '__main__':
    diagnostic_result = run_diagnostic()
    assert isinstance(diagnostic_result, dict)
    assert 'status' in diagnostic_result
    assert 'mcp_count' in diagnostic_result
    assert 'populator_health_status' in diagnostic_result
    assert 'recent_errors' in diagnostic_result
    assert 'diagnosis_message' in diagnostic_result
    print('PASS')