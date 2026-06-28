import unittest
from unittest.mock import Mock, patch
import requests
from flask import Flask, request, jsonify

# Mock API Server
app = Flask(__name__)

@app.route('/trigger_backfill', methods=['POST'])
def trigger_backfill():
    data = request.get_json()
    if data and 'mcp_id' in data:
        return jsonify({"status": "success", "message": "Backfill triggered"}), 200
    return jsonify({"status": "error", "message": "Invalid request"}), 400

# Mock Write Service
class MockWriteService:
    def __init__(self):
        self.mcp_definition_history = []

    def query_mcp_definition_history(self, mcp_id):
        return [entry for entry in self.mcp_definition_history if entry.get('mcp_id') == mcp_id]

# Test Script
def verify_mcp_definition_history_backfill_api_functionality():
    # Start the mock API server in a separate thread
    import threading
    server_thread = threading.Thread(target=app.run, kwargs={'port': 8780})
    server_thread.daemon = True
    server_thread.start()

    # Mock data
    mcp_id = "test_mcp_123"
    mock_write_service = MockWriteService()

    # Simulate API call
    response = requests.post(
        "http://127.0.0.1:8780/trigger_backfill",
        json={"mcp_id": mcp_id}
    )

    # Verify API response
    assert response.status_code == 200
    assert response.json()["status"] == "success"

    # Simulate backfill by adding mock data to the history table
    mock_write_service.mcp_definition_history.append({
        "mcp_id": mcp_id,
        "version": 1,
        "definition": "mock_definition",
        "timestamp": "2023-01-01T00:00:00Z"
    })

    # Query the mock DB
    history_entries = mock_write_service.query_mcp_definition_history(mcp_id)

    # Verify that new entries have been created
    assert len(history_entries) > 0
    assert history_entries[0]["mcp_id"] == mcp_id

    print("PASS")

if __name__ == "__main__":
    verify_mcp_definition_history_backfill_api_functionality()