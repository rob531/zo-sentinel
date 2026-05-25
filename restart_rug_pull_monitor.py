import json
from datetime import datetime
import requests
from typing import List
import os
import subprocess
import logging

class ServiceRestarter:
    def __init__(self):
        self.log_file = 'service_restarter.log'
        self.pid_file_path = '/var/run/rug_pull_monitor.pid'

    def check_service_status(self) -> bool:
        try:
            with open(self.pid_file_path, 'r') as pid_file:
                pid = int(pid_file.read())
            process = subprocess.check_output(['ps', '-p', str(pid)], stdout=subprocess.PIPE)
            if b'ZO-SENTINEL' in process.decode('utf-8'):
                return True
        except FileNotFoundError:
            pass

    def diagnose_heartbeat_loop(self) -> bool:
        response = requests.post('http://127.0.0.1:8772/write', json={'table': 'service_health', 'rows': {'service': 'rug_pull_monitor', 'last_heartbeat': datetime.now().isoformat()}})
        if not response.status_code == 200 or 'rows' not in response.json():
            return False
        heartbeat_row = response.json()['rows'][0]
        last_heartbeat_timestamp = datetime.fromisoformat(heartbeat_row['last_heartbeat'])
        time_diff = (datetime.now() - last_heartbeat_timestamp).total_seconds()
        if time_diff > 10 * 60:
            return True
        return False

    def restart_service(self):
        logging.basicConfig(filename=self.log_file, level=logging.INFO)
        try:
            with open(self.pid_file_path, 'r') as pid_file:
                pid = int(pid_file.read())
            subprocess.check_output(['sudo', 'kill', str(pid)])
            subprocess.check_output(['sudo', 'rm', self.pid_file_path])
            print('Rug pull monitor service restarted.')
            logging.info('Service restarted successfully.')
        except FileNotFoundError:
            print('No process running with ID in PID file')
            logging.error('No process running, no restart necessary.')

def main():
    restarter = ServiceRestarter()
    if not restarter.check_service_status() or restarter.diagnose_heartbeat_loop():
        restarter.restart_service()

if __name__ == '__main__':
    run()