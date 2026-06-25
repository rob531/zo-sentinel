import requests
import json
from datetime import datetime, timedelta

def get_mcp_definition_history_status():
    # Query mcp_definition_history for row count and latest timestamp
    query = """
    SELECT COUNT(*) as row_count, MAX(created_at) as last_entry_timestamp
    FROM mcp_definition_history
    """
    response = requests.post("http://127.0.0.1:8772/query", json={"query": query})
    if response.status_code != 200:
        return {"error": "Failed to query mcp_definition_history"}

    history_data = response.json()["data"][0]
    row_count = history_data["row_count"]
    last_entry_timestamp = history_data["last_entry_timestamp"]

    # Query mcp_submissions for pending definitions
    submissions_query = """
    SELECT COUNT(*) as mcp_submissions_count
    FROM mcp_submissions
    WHERE status = 'pending'
    """
    submissions_response = requests.post("http://127.0.0.1:8772/query", json={"query": submissions_query})
    if submissions_response.status_code != 200:
        return {"error": "Failed to query mcp_submissions"}

    submissions_data = submissions_response.json()["data"][0]
    mcp_submissions_count = submissions_data["mcp_submissions_count"]

    # Determine status message
    status_message = "healthy"
    if row_count == 0:
        status_message = "empty"
    else:
        last_entry_time = datetime.fromisoformat(last_entry_timestamp.replace('Z', '+00:00'))
        if (datetime.utcnow() - last_entry_time) > timedelta(hours=24):
            status_message = "stale"

    return {
        "row_count": row_count,
        "last_entry_timestamp": last_entry_timestamp,
        "mcp_submissions_count": mcp_submissions_count,
        "status_message": status_message
    }

if __name__ == "__main__":
    report = get_mcp_definition_history_status()
    expected_keys = ["row_count", "last_entry_timestamp", "mcp_submissions_count", "status_message"]
    assert all(key in report for key in expected_keys), "Missing expected keys in report"
    assert report["status_message"] in ["healthy", "empty", "stale"], "Invalid status_message"
    print("PASS")