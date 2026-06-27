import requests
import time
import threading
import sqlite3
from datetime import datetime

class MCPDefinitionHistoryBackfillDaemon:
    def __init__(self):
        self.write_service_url = 'http://127.0.0.1:8772'
        self.heartbeat_interval = 60
        self.running = False
        self.heartbeat_thread = None

    def start(self):
        self.running = True
        self.heartbeat_thread = threading.Thread(target=self._heartbeat)
        self.heartbeat_thread.start()
        self._backfill()

    def stop(self):
        self.running = False
        if self.heartbeat_thread:
            self.heartbeat_thread.join()

    def _heartbeat(self):
        while self.running:
            self._update_service_health()
            time.sleep(self.heartbeat_interval)

    def _update_service_health(self):
        query = {
            'table': 'service_health',
            'data': {
                'service_name': 'mcp_definition_history_backfill_daemon',
                'last_heartbeat': datetime.now().isoformat()
            }
        }
        response = requests.post(f'{self.write_service_url}/write', json=query)
        if response.status_code != 200:
            print(f'Failed to update service health: {response.text}')

    def _backfill(self):
        # Query mcp_server_registry for MCPs without entries in mcp_definition_history
        query = {
            'table': 'mcp_server_registry',
            'columns': ['mcp_id', 'definition'],
            'where': 'mcp_id NOT IN (SELECT mcp_id FROM mcp_definition_history)'
        }
        response = requests.post(f'{self.write_service_url}/query', json=query)
        if response.status_code != 200:
            print(f'Failed to query mcp_server_registry: {response.text}')
            return

        mcps = response.json()
        for mcp in mcps:
            # Insert historical definition data into mcp_definition_history
            insert_query = {
                'table': 'mcp_definition_history',
                'data': {
                    'mcp_id': mcp['mcp_id'],
                    'definition': mcp['definition'],
                    'timestamp': datetime.now().isoformat()
                }
            }
            insert_response = requests.post(f'{self.write_service_url}/write', json=insert_query)
            if insert_response.status_code != 200:
                print(f'Failed to insert into mcp_definition_history: {insert_response.text}')

if __name__ == '__main__':
    # Simulate a backfill run against an in-memory or test database
    conn = sqlite3.connect(':memory:')
    cursor = conn.cursor()

    # Create test tables
    cursor.execute('''
        CREATE TABLE mcp_server_registry (
            mcp_id TEXT PRIMARY KEY,
            definition TEXT
        )
    ''')
    cursor.execute('''
        CREATE TABLE mcp_definition_history (
            mcp_id TEXT,
            definition TEXT,
            timestamp TEXT,
            PRIMARY KEY (mcp_id, timestamp)
        )
    ''')
    cursor.execute('''
        CREATE TABLE service_health (
            service_name TEXT PRIMARY KEY,
            last_heartbeat TEXT
        )
    ''')

    # Insert test data
    cursor.execute('INSERT INTO mcp_server_registry (mcp_id, definition) VALUES (?, ?)', ('mcp1', 'definition1'))
    cursor.execute('INSERT INTO mcp_server_registry (mcp_id, definition) VALUES (?, ?)', ('mcp2', 'definition2'))
    conn.commit()

    # Start the daemon
    daemon = MCPDefinitionHistoryBackfillDaemon()
    daemon.start()

    # Wait for the backfill to complete
    time.sleep(2)

    # Verify the backfill
    cursor.execute('SELECT COUNT(*) FROM mcp_definition_history')
    count = cursor.fetchone()[0]
    if count == 2:
        print('PASS')
    else:
        print('FAIL')

    # Clean up
    daemon.stop()
    conn.close()