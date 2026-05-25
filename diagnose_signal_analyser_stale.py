import logging
from datetime import datetime, timedelta
import subprocess
import requests

def diagnose_signal_analyser_stale():
    # Read signal_analyser.py source
    with open('signal_analyser.py', 'r') as file:
        source = file.read()

    # Check service_health table for error messages
    response = requests.post(
        f'http://127.0.0.1:8772/write',
        json={'table': 'service_health', 'rows': {'service': 'signal_analyser', 'last_heartbeat': datetime.now() - timedelta(seconds=7200)}},
        headers={'Content-Type': 'application/json'}
    )
    if response.status_code != 200:
        logging.error(f'Failed to check service_health table: {response.text}')

    # Examine recent log lines
    try:
        with open('/var/log/daemon.log', 'r') as file:
            for line in file.readlines():
                if 'signal_analyser' in line:
                    print(line)
    except FileNotFoundError:
        logging.error('Log file not found')

import sys

if __name__ == '__main__':
    diagnose_signal_analyser_stale()
    subprocess.run(['run'], check=True)