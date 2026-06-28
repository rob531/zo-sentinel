import requests
from datetime import datetime, timedelta
import sqlite3
from typing import Optional

# Mock database setup for self-testing
def setup_mock_db():
    conn = sqlite3.connect(':memory:')
    cursor = conn.cursor()

    # Create tables
    cursor.execute('''
    CREATE TABLE mcp_signal_scores (
        id INTEGER PRIMARY KEY,
        timestamp DATETIME,
        score REAL
    )
    ''')

    cursor.execute('''
    CREATE TABLE mcp_threat_associations (
        id INTEGER PRIMARY KEY,
        timestamp DATETIME,
        threat_id TEXT,
        association_type TEXT
    )
    ''')

    # Insert mock data (some recent, some old)
    recent_time = datetime.now().isoformat()
    old_time = (datetime.now() - timedelta(hours=2)).isoformat()

    cursor.execute('''
    INSERT INTO mcp_signal_scores (timestamp, score)
    VALUES (?, ?), (?, ?)
    ''', (recent_time, 0.95), (old_time, 0.85))

    cursor.execute('''
    INSERT INTO mcp_threat_associations (timestamp, threat_id, association_type)
    VALUES (?, ?, ?), (?, ?, ?)
    ''', (recent_time, 'threat1', 'typeA'), (old_time, 'threat2', 'typeB'))

    conn.commit()
    return conn

# Database query function
def query_recent_updates(db_url: str, freshness_threshold: timedelta) -> bool:
    """
    Query the database for recent updates in relevant tables.
    Returns True if recent updates are found, False otherwise.
    """
    try:
        # Calculate the threshold time
        threshold_time = (datetime.now() - freshness_threshold).isoformat()

        # Query for recent updates in mcp_signal_scores
        response = requests.post(
            db_url,
            json={
                "query": f"""
                SELECT COUNT(*) FROM mcp_signal_scores
                WHERE timestamp >= '{threshold_time}'
                """,
                "variables": {}
            }
        )
        response.raise_for_status()
        signal_count = response.json()['data'][0][0]

        # Query for recent updates in mcp_threat_associations
        response = requests.post(
            db_url,
            json={
                "query": f"""
                SELECT COUNT(*) FROM mcp_threat_associations
                WHERE timestamp >= '{threshold_time}'
                """,
                "variables": {}
            }
        )
        response.raise_for_status()
        threat_count = response.json()['data'][0][0]

        # Check if either table has recent updates
        return signal_count > 0 or threat_count > 0

    except requests.exceptions.RequestException as e:
        print(f"Error querying database: {e}")
        return False

def verify_rug_pull_monitor_active(db_url: str, freshness_threshold: timedelta = timedelta(hours=1)) -> str:
    """
    Verify that the rug_pull_monitor daemon is actively processing data.
    Returns 'PASS' if recent activity is detected, 'FAIL' otherwise.
    """
    if query_recent_updates(db_url, freshness_threshold):
        return "PASS"
    else:
        return "FAIL"

# Self-test function
def self_test():
    print("Running self-test with mock database...")
    conn = setup_mock_db()

    # Mock requests.post to use our in-memory DB
    def mock_post(url, json):
        cursor = conn.cursor()
        cursor.execute(json['query'])
        result = cursor.fetchall()
        return type('Response', (), {
            'json': lambda: {'data': result},
            'raise_for_status': lambda: None
        })()

    original_post = requests.post
    requests.post = mock_post

    try:
        # Test with default threshold (1 hour)
        result = verify_rug_pull_monitor_active("mock_url")
        print(f"Self-test result: {result} (expected PASS)")

        # Test with stricter threshold (10 minutes)
        result = verify_rug_pull_monitor_active("mock_url", timedelta(minutes=10))
        print(f"Self-test result with stricter threshold: {result} (expected FAIL)")

    finally:
        requests.post = original_post
        conn.close()

if __name__ == "__main__":
    # Example usage with real database
    # DB_URL = "http://write_service:8080/query"
    # result = verify_rug_pull_monitor_active(DB_URL)
    # print(result)

    # Run self-test
    self_test()