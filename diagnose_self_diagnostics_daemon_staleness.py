import requests
import time
from datetime import datetime, timedelta

def run_diagnostic():
    url = "http://127.0.0.1:8772/query"
    headers = {'Content-Type': 'application/json'}
    payload = {
        "query": "SELECT last_heartbeat FROM service_health WHERE service_name = 'self_diagnostics'"
    }

    max_retries = 3
    retry_delay = 1  # seconds

    for attempt in range(max_retries):
        try:
            response = requests.post(url, headers=headers, json=payload, timeout=5)
            response.raise_for_status()
            break
        except requests.exceptions.RequestException as e:
            if attempt == max_retries - 1:
                return {
                    'status': 'FAIL',
                    'error': f"Failed to query service_health table after {max_retries} attempts: {str(e)}"
                }
            time.sleep(retry_delay)
            retry_delay *= 2

    try:
        data = response.json()
        last_heartbeat_str = data['results'][0]['last_heartbeat']
        last_heartbeat = datetime.strptime(last_heartbeat_str, '%Y-%m-%d %H:%M:%S')
        current_time = datetime.now()
        staleness_duration = current_time - last_heartbeat

        # Query other system health metrics
        other_metrics_payload = {
            "query": "SELECT * FROM system_health_metrics"
        }
        other_metrics_response = requests.post(url, headers=headers, json=other_metrics_payload, timeout=5)
        other_metrics_response.raise_for_status()
        other_metrics_data = other_metrics_response.json()

        # Analyze other metrics for potential root causes
        anomalies = []
        for metric in other_metrics_data['results']:
            if metric['status'] != 'healthy':
                anomalies.append(f"{metric['metric_name']} is {metric['status']}")

        result = {
            'status': 'healthy' if staleness_duration < timedelta(minutes=5) else 'stale',
            'last_heartbeat': last_heartbeat_str,
            'staleness_duration_seconds': staleness_duration.total_seconds(),
            'anomalies': anomalies if anomalies else None
        }
        return result
    except (KeyError, IndexError, ValueError) as e:
        return {
            'status': 'FAIL',
            'error': f"Failed to parse response: {str(e)}"
        }

if __name__ == "__main__":
    diagnostic_result = run_diagnostic()
    expected_keys = {'status', 'staleness_duration_seconds'}

    if all(key in diagnostic_result for key in expected_keys):
        print("PASS: Diagnostic completed successfully.")
        print("Diagnostic Result:", diagnostic_result)
    else:
        print("FAIL: Diagnostic did not complete successfully.")
        print("Diagnostic Result:", diagnostic_result)