import requests
from datetime import datetime, timedelta
import time
import random
from typing import Dict

CRITICAL_DAEMONS = [
    'write_service',
    'inference_router',
    'gate_scheduler',
    'self_diagnostics',
    'rug_pull_monitor',
    'gate_orchestrator'
]

DEFAULT_HEARTBEAT_THRESHOLD = 60  # seconds

def verify_heartbeats(threshold: int = DEFAULT_HEARTBEAT_THRESHOLD) -> Dict[str, bool]:
    """
    Verify the heartbeat status of critical daemons by querying the service_health table.

    Args:
        threshold: Maximum allowed age of heartbeat in seconds (default: 60).

    Returns:
        Dictionary mapping daemon names to boolean indicating if heartbeat is recent.
    """
    query = """
        SELECT daemon_name, last_heartbeat
        FROM service_health
        WHERE daemon_name IN ({daemon_names})
    """
    daemon_names = ','.join([f"'{daemon}'" for daemon in CRITICAL_DAEMONS])
    params = {'daemon_names': daemon_names}

    try:
        response = requests.post(
            'http://127.0.0.1:8772/query',
            json={'query': query, 'params': params}
        )
        response.raise_for_status()
        data = response.json()

        heartbeat_status = {}
        current_time = datetime.now()

        for row in data:
            daemon_name = row['daemon_name']
            last_heartbeat = datetime.fromisoformat(row['last_heartbeat'])
            is_recent = (current_time - last_heartbeat) <= timedelta(seconds=threshold)
            heartbeat_status[daemon_name] = is_recent

        # Ensure all critical daemons are accounted for
        for daemon in CRITICAL_DAEMONS:
            if daemon not in heartbeat_status:
                heartbeat_status[daemon] = False

        return heartbeat_status

    except requests.RequestException as e:
        print(f"Error querying service health: {e}")
        return {daemon: False for daemon in CRITICAL_DAEMONS}

def simulate_service_health() -> None:
    """
    Simulate a service_health table for testing purposes.
    """
    print("Simulating service_health table...")
    from flask import Flask, request, jsonify

    app = Flask(__name__)

    # Mock data
    mock_data = [
        {'daemon_name': 'write_service', 'last_heartbeat': (datetime.now() - timedelta(seconds=30)).isoformat()},
        {'daemon_name': 'inference_router', 'last_heartbeat': (datetime.now() - timedelta(seconds=20)).isoformat()},
        {'daemon_name': 'gate_scheduler', 'last_heartbeat': (datetime.now() - timedelta(seconds=10)).isoformat()},
        {'daemon_name': 'self_diagnostics', 'last_heartbeat': (datetime.now() - timedelta(seconds=5)).isoformat()},
        {'daemon_name': 'rug_pull_monitor', 'last_heartbeat': (datetime.now() - timedelta(seconds=65)).isoformat()},
        {'daemon_name': 'gate_orchestrator', 'last_heartbeat': (datetime.now() - timedelta(seconds=40)).isoformat()},
    ]

    @app.route('/query', methods=['POST'])
    def query():
        data = request.get_json()
        query = data.get('query', '')
        params = data.get('params', {})

        if 'daemon_names' in params:
            daemon_names = [name.strip("'") for name in params['daemon_names'].split(',')]
            filtered_data = [row for row in mock_data if row['daemon_name'] in daemon_names]
            return jsonify(filtered_data)

        return jsonify([])

    # Run the mock server in a separate thread
    import threading
    mock_server_thread = threading.Thread(target=app.run, kwargs={'port': 8772})
    mock_server_thread.daemon = True
    mock_server_thread.start()

    # Wait for the server to start
    time.sleep(1)

if __name__ == '__main__':
    # Simulate the service_health table if run in isolation
    simulate_service_health()

    # Verify heartbeats
    heartbeat_status = verify_heartbeats()

    # Print status of each critical daemon
    print("\nDaemon Heartbeat Status:")
    for daemon, is_healthy in heartbeat_status.items():
        status = "HEALTHY" if is_healthy else "UNHEALTHY"
        print(f"{daemon}: {status}")

    # Assert that at least one daemon is healthy
    assert any(heartbeat_status.values()), "No healthy daemons found!"
    print("\nPASS")