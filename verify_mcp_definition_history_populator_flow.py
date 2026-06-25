import sqlite3
from datetime import datetime
from typing import List, Dict, Any

# Mock MCP submission data
MOCK_MCP_SUBMISSIONS = [
    {
        "mcp_id": "MCP123",
        "definition": {"key1": "value1", "key2": "value2"},
        "submitted_at": datetime.now().isoformat(),
        "submitted_by": "user1"
    },
    {
        "mcp_id": "MCP456",
        "definition": {"keyA": "valueA", "keyB": "valueB"},
        "submitted_at": datetime.now().isoformat(),
        "submitted_by": "user2"
    }
]

def create_in_memory_db() -> sqlite3.Connection:
    """Create an in-memory SQLite database with the required tables."""
    conn = sqlite3.connect(":memory:")
    cursor = conn.cursor()

    # Create mcp_submissions table
    cursor.execute("""
    CREATE TABLE mcp_submissions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        mcp_id TEXT NOT NULL,
        definition TEXT NOT NULL,
        submitted_at TEXT NOT NULL,
        submitted_by TEXT NOT NULL
    )
    """)

    # Create mcp_definition_history table
    cursor.execute("""
    CREATE TABLE mcp_definition_history (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        mcp_id TEXT NOT NULL,
        definition TEXT NOT NULL,
        created_at TEXT NOT NULL,
        created_by TEXT NOT NULL
    )
    """)

    conn.commit()
    return conn

def insert_mock_submissions(conn: sqlite3.Connection, submissions: List[Dict[str, Any]]) -> None:
    """Insert mock MCP submissions into the database."""
    cursor = conn.cursor()
    for submission in submissions:
        cursor.execute("""
        INSERT INTO mcp_submissions (mcp_id, definition, submitted_at, submitted_by)
        VALUES (?, ?, ?, ?)
        """, (
            submission["mcp_id"],
            str(submission["definition"]),
            submission["submitted_at"],
            submission["submitted_by"]
        ))
    conn.commit()

def populate_mcp_definition_history(conn: sqlite3.Connection) -> None:
    """Simulate the mcp_definition_history_populator logic."""
    cursor = conn.cursor()

    # Get all submissions from mcp_submissions
    cursor.execute("SELECT mcp_id, definition, submitted_at, submitted_by FROM mcp_submissions")
    submissions = cursor.fetchall()

    # Insert into mcp_definition_history
    for submission in submissions:
        cursor.execute("""
        INSERT INTO mcp_definition_history (mcp_id, definition, created_at, created_by)
        VALUES (?, ?, ?, ?)
        """, (
            submission[0],
            submission[1],
            submission[2],
            submission[3]
        ))

    conn.commit()

def verify_mcp_definition_history(conn: sqlite3.Connection, expected_submissions: List[Dict[str, Any]]) -> bool:
    """Verify that mcp_definition_history contains the expected entries."""
    cursor = conn.cursor()
    cursor.execute("SELECT mcp_id, definition, created_at, created_by FROM mcp_definition_history")
    history_entries = cursor.fetchall()

    if len(history_entries) != len(expected_submissions):
        return False

    for entry, submission in zip(history_entries, expected_submissions):
        if (entry[0] != submission["mcp_id"] or
            entry[1] != str(submission["definition"]) or
            entry[2] != submission["submitted_at"] or
            entry[3] != submission["submitted_by"]):
            return False

    return True

if __name__ == "__main__":
    # Create in-memory database
    conn = create_in_memory_db()

    # Test case 1: Empty mcp_submissions
    populate_mcp_definition_history(conn)
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM mcp_definition_history")
    count = cursor.fetchone()[0]
    if count == 0:
        print("PASS: mcp_definition_history is empty when mcp_submissions is empty")
    else:
        print("FAIL: mcp_definition_history is not empty when mcp_submissions is empty")
        exit(1)

    # Test case 2: mcp_submissions with data
    insert_mock_submissions(conn, MOCK_MCP_SUBMISSIONS)
    populate_mcp_definition_history(conn)

    if verify_mcp_definition_history(conn, MOCK_MCP_SUBMISSIONS):
        print("PASS: mcp_definition_history correctly populated from mcp_submissions")
    else:
        print("FAIL: mcp_definition_history not correctly populated from mcp_submissions")
        exit(1)

    conn.close()