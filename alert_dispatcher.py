import requests
from datetime import datetime, timezone

WRITE_SERVICE_URL = 'http://127.0.0.1:8772'

def ws_write(table, rows):
    r = requests.post(WRITE_SERVICE_URL + '/write',
        json={'table': table, 'rows': rows, 'wait': True}, timeout=8)
    return r.status_code == 200

def ws_query(sql):
    r = requests.post(WRITE_SERVICE_URL + '/query', json={'sql': sql}, timeout=8)
    return r.json().get('rows', []) if r.status_code == 200 else []

def dispatch_alert(level, agent_id, message, metadata=None):
    event_data = {
        'event_type': 'alert',
        'level': level,
        'agent_id': agent_id,
        'message': message,
        'metadata': metadata
    }
    ws_write('mesh_events', event_data)

def get_recent_alerts(limit=10):
    sql_query = f"SELECT * FROM mesh_events WHERE event_type='alert' ORDER BY last_heartbeat DESC LIMIT {limit}"
    return ws_query(sql_query)