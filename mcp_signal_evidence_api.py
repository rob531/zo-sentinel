# mcp_signal_evidence_api.py

from fastapi import FastAPI, Depends, HTTPException
from pydantic import BaseModel
from typing import Dict, Any, List
import json
import sqlite3 # Used for the in-memory test database

# --- Database Interaction Layer ---
# For the self-test, we'll use SQLite's in-memory database.
# In a production environment, this would typically be an asynchronous
# connection pool to a PostgreSQL database (e.g., using asyncpg or SQLAlchemy).

def get_db_connection():
    """
    Establishes a database connection.
    For the self-test, this returns an in-memory SQLite connection.
    In a real application, this would connect to a persistent database.
    """
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row  # Allows accessing columns by name
    return conn

def create_tables(conn: sqlite3.Connection):
    """
    Creates the necessary tables for the test database.
    The `evidence_blob` is stored as TEXT, which is compatible with
    Postgres's JSONB type (when parsed/serialized correctly) and SQLite.
    """
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS mcp_signal_scores (
            server_id INTEGER NOT NULL,
            signal_type TEXT NOT NULL,
            evidence_blob TEXT NOT NULL, -- Stores JSON as TEXT
            PRIMARY KEY (server_id, signal_type)
        );
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS mcp_signal_enrichments (
            server_id INTEGER NOT NULL,
            signal_type TEXT NOT NULL,
            evidence_blob TEXT NOT NULL, -- Stores JSON as TEXT
            PRIMARY KEY (server_id, signal_type)
        );
    """)
    conn.commit()

def seed_data(conn: sqlite3.Connection, server_id: int):
    """
    Seeds the database with sample data for a given server_id.
    """
    cursor = conn.cursor()

    # Sample data for mcp_signal_scores
    cursor.execute("""
        INSERT INTO mcp_signal_scores (server_id, signal_type, evidence_blob)
        VALUES (?, ?, ?)
    """, (server_id, "cpu_usage_score", json.dumps({"metric": "cpu", "value": 85, "threshold": 80})))
    cursor.execute("""
        INSERT INTO mcp_signal_scores (server_id, signal_type, evidence_blob)
        VALUES (?, ?, ?)
    """, (server_id, "memory_leak_score", json.dumps({"metric": "memory", "leak_detected": True, "usage_gb": 12})))

    # Sample data for mcp_signal_enrichments
    cursor.execute("""
        INSERT INTO mcp_signal_enrichments (server_id, signal_type, evidence_blob)
        VALUES (?, ?, ?)
    """, (server_id, "process_list_enrichment", json.dumps({"processes": ["nginx", "python", "java"]})))
    cursor.execute("""
        INSERT INTO mcp_signal_enrichments (server_id, signal_type, evidence_blob)
        VALUES (?, ?, ?)
    """, (server_id, "network_activity_enrichment", json.dumps({"connections": 150, "bytes_in": "10GB"})))

    # Add data for another server_id to ensure data isolation in queries
    cursor.execute("""
        INSERT INTO mcp_signal_scores (server_id, signal_type, evidence_blob)
        VALUES (?, ?, ?)
    """, (server_id + 1, "disk_io_score", json.dumps({"metric": "disk_io", "iops": 500})))

    conn.commit()

def fetch_signal_evidence(conn: sqlite3.Connection, server_id: int) -> Dict[str, Any]:
    """
    Fetches all signal evidence (scores and enrichments) for a given server_id
    from the database. Parses the evidence_blob JSON.
    """
    evidence: Dict[str, Any] = {}
    cursor = conn.cursor()

    # Fetch from mcp_signal_scores
    cursor.execute(
        "SELECT signal_type, evidence_blob FROM mcp_signal_scores WHERE server_id = ?",
        (server_id,)
    )
    for row in cursor.fetchall():
        try:
            evidence[row["signal_type"]] = json.loads(row["evidence_blob"])
        except json.JSONDecodeError:
            # Handle cases where evidence_blob might be malformed JSON
            print(f"Warning: Malformed JSON for signal_type '{row['signal_type']}' "
                  f"in mcp_signal_scores for server_id {server_id}. Raw: {row['evidence_blob']}")
            evidence[row["signal_type"]] = {"error": "Malformed JSON", "raw_blob": row["evidence_blob"]}

    # Fetch from mcp_signal_enrichments
    cursor.execute(
        "SELECT signal_type, evidence_blob FROM mcp_signal_enrichments WHERE server_id = ?",
        (server_id,)
    )
    for row in cursor.fetchall():
        try:
            evidence[row["signal_type"]] = json.loads(row["evidence_blob"])
        except json.JSONDecodeError:
            # Handle cases where evidence_blob might be malformed JSON
            print(f"Warning: Malformed JSON for signal_type '{row['signal_type']}' "
                  f"in mcp_signal_enrichments for server_id {server_id}. Raw: {row['evidence_blob']}")
            evidence[row["signal_type"]] = {"error": "Malformed JSON", "raw_blob": row["evidence_blob"]}

    return evidence

# --- FastAPI Application ---
app = FastAPI(
    title="MCP Signal Evidence API",
    description="API to retrieve signal evidence (scores and enrichments) for servers."
)

@app.get("/mcp/{server_id}/signal_evidence", response_model=Dict[str, Any])
async def get_server_signal_evidence(
    server_id: int,
    db: sqlite3.Connection = Depends(get_db_connection) # Dependency injection for DB connection
) -> Dict[str, Any]:
    """
    Retrieves all signal evidence (scores and enrichments) for a given server_id.
    Returns a dictionary where keys are signal types and values are the evidence_blob JSON.
    """
    if server_id <= 0:
        raise HTTPException(status_code=400, detail="Server ID must be a positive integer.")

    evidence = fetch_signal_evidence(db, server_id)

    # If no evidence is found, an empty dictionary is returned, as per the prompt.
    return evidence

# --- Self-Test Block ---
if __name__ == "__main__":
    from fastapi.testclient import TestClient

    # 1. Setup in-memory database
    test_conn = get_db_connection()
    create_tables(test_conn)
    TEST_SERVER_ID = 123
    seed_data(test_conn, TEST_SERVER_ID)

    # 2. Override the database dependency for the TestClient
    def override_get_db_connection():
        """Provides the pre-seeded in-memory connection to the FastAPI app."""
        yield test_conn

    app.dependency_overrides[get_db_connection] = override_get_db_connection

    # 3. Initialize TestClient
    client = TestClient(app)

    # 4. Test Case 1: Retrieve evidence for a seeded server_id
    print(f"Testing GET /mcp/{TEST_SERVER_ID}/signal_evidence...")
    response = client.get(f"/mcp/{TEST_SERVER_ID}/signal_evidence")

    # Assertions for Test Case 1
    assert response.status_code == 200, f"Expected status code 200, got {response.status_code}"
    response_data = response.json()

    expected_evidence = {
        "cpu_usage_score": {"metric": "cpu", "value": 85, "threshold": 80},
        "memory_leak_score": {"metric": "memory", "leak_detected": True, "usage_gb": 12},
        "process_list_enrichment": {"processes": ["nginx", "python", "java"]},
        "network_activity_enrichment": {"connections": 150, "bytes_in": "10GB"}
    }

    # Compare keys and then the full dictionary for robustness
    assert sorted(response_data.keys()) == sorted(expected_evidence.keys()), \
        f"Keys mismatch. Expected: {sorted(expected_evidence.keys())}, Got: {sorted(response_data.keys())}"
    assert response_data == expected_evidence, \
        f"Response data mismatch.\nExpected: {expected_evidence}\nGot: {response_data}"
    print("Test Case 1: Successfully retrieved expected signal evidence.")

    # 5. Test Case 2: Retrieve evidence for a server_id with no data
    NO_DATA_SERVER_ID = TEST_SERVER_ID + 999
    print(f"Testing GET /mcp/{NO_DATA_SERVER_ID}/signal_evidence (no data)...")
    response_no_data = client.get(f"/mcp/{NO_DATA_SERVER_ID}/signal_evidence")

    # Assertions for Test Case 2
    assert response_no_data.status_code == 200, \
        f"Expected status code 200 for no data, got {response_no_data.status_code}"
    assert response_no_data.json() == {}, \
        f"Expected empty dict for no data, got {response_no_data.json()}"
    print("Test Case 2: Successfully handled server_id with no signal evidence.")

    # 6. Test Case 3: Invalid server_id (e.g., non-positive)
    INVALID_SERVER_ID = 0
    print(f"Testing GET /mcp/{INVALID_SERVER_ID}/signal_evidence (invalid ID)...")
    response_invalid_id = client.get(f"/mcp/{INVALID_SERVER_ID}/signal_evidence")

    # Assertions for Test Case 3
    assert response_invalid_id.status_code == 400, \
        f"Expected status code 400 for invalid ID, got {response_invalid_id.status_code}"
    assert response_invalid_id.json() == {"detail": "Server ID must be a positive integer."}, \
        f"Expected error detail mismatch, got {response_invalid_id.json()}"
    print("Test Case 3: Successfully handled invalid server_id.")

    print("\nPASS")