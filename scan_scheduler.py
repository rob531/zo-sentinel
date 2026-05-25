import os
import json
import requests
from datetime import datetime
import logging

# Configure logging setup
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

def schedule_scan(server_id, priority=0.5):
    url = 'http://127.0.0.1:8772/write'
    data = {
        'table': 'scan_requests',
        'rows': [{'server_id': server_id, 'priority': priority}],
        'wait': True
    }
    response = requests.post(url, json=data)
    if not response.status_code == 200:
        raise Exception(f'Failed to write to write_service: {response.text}')

def get_pending_scans(limit=20):
    url = 'http://127.0.0.1:8772/query'
    data = {
        'table': 'scan_requests',
        'rows': {'memory_type': 'scan_request', 'limit': limit},
        'wait': True
    }
    response = requests.post(url, json=data)
    if not response.status_code == 200:
        raise Exception(f'Failed to query write_service: {response.text}')
    return response.json()['data']

def mark_scan_complete(server_id):
    url = 'http://127.0.0.1:8772/write'
    data = {
        'table': 'scan_requests',
        'rows': [{'server_id': server_id, 'memory_type': 'scan_complete'}],
        'wait': True
    }
    response = requests.post(url, json=data)
    if not response.status_code == 200:
        raise Exception(f'Failed to write to write_service: {response.text}')

def run():
    logging.info('Starting scan scheduler')
    while True:
        pending_scans = get_pending_scans()
        for server_id in [scan['server_id'] for scan in pending_scans]:
            schedule_scan(server_id)
        mark_scan_complete(0)  # mark scan as complete
        # Add additional logic here to handle heartbeats and other scheduling tasks

if __name__ == '__main__':
    run()