from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
import requests
from datetime import datetime, timedelta
import json

app = FastAPI()

# Mock database tables
service_health = {
    'mcp_scanner': {
        'last_heartbeat': datetime.now().isoformat(),
        'status': 'running'
    }
}

mcp_submissions = [
    {'timestamp': datetime.now().isoformat()}
]

@app.get("/mcp_scanner/status")
async def get_mcp_scanner_status():
    # Query service_health table for mcp_scanner's last heartbeat and status
    scanner_data = service_health.get('mcp_scanner', {})
    last_heartbeat_timestamp = scanner_data.get('last_heartbeat')
    scanner_status = scanner_data.get('status', 'unknown')

    # Determine if the scanner is stale (no heartbeat in the last 5 minutes)
    if last_heartbeat_timestamp:
        last_heartbeat = datetime.fromisoformat(last_heartbeat_timestamp)
        if datetime.now() - last_heartbeat > timedelta(minutes=5):
            scanner_status = 'stale'

    # Query mcp_submissions table for current row count and latest submission timestamp
    submission_count = len(mcp_submissions)
    last_submission_timestamp = mcp_submissions[-1]['timestamp'] if mcp_submissions else None

    # Return the JSON object
    return {
        'scanner_status': scanner_status,
        'last_heartbeat_timestamp': last_heartbeat_timestamp,
        'submission_count': submission_count,
        'last_submission_timestamp': last_submission_timestamp
    }

if __name__ == "__main__":
    client = TestClient(app)

    # Test case 1: Active scanner with submissions
    response = client.get("/mcp_scanner/status")
    assert response.status_code == 200
    data = response.json()
    assert 'scanner_status' in data
    assert 'last_heartbeat_timestamp' in data
    assert 'submission_count' in data
    assert 'last_submission_timestamp' in data

    # Test case 2: No submissions
    mcp_submissions.clear()
    response = client.get("/mcp_scanner/status")
    assert response.status_code == 200
    data = response.json()
    assert data['submission_count'] == 0
    assert data['last_submission_timestamp'] is None

    print("PASS")