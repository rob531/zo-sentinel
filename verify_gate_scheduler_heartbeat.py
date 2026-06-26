import requests
import time

def verify_gate_scheduler_heartbeat():
    # Query the service_health table for the gate_scheduler entry
    url = 'http://write_service/service_health'
    data = {'service': 'gate_scheduler'}
    response = requests.post(url, json=data)

    if response.status_code != 200:
        return False, {'error': 'Failed to query service_health table'}

    entries = response.json()
    if not entries:
        return False, {'error': 'No entry found for gate_scheduler'}

    # Check the last_heartbeat timestamp
    last_heartbeat = entries[0]['last_heartbeat']
    current_time = time.time()
    if current_time - last_heartbeat > 180:
        return False, {'last_heartbeat': last_heartbeat, 'current_time': current_time}

    return True, {'last_heartbeat': last_heartbeat, 'current_time': current_time}

if __name__ == '__main__':
    is_healthy, evidence = verify_gate_scheduler_heartbeat()
    if is_healthy:
        print('PASS')
    else:
        print('FAIL')