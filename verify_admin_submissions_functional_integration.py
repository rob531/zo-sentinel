import requests
import sqlite3
import json
import os

# Configuration
API_BASE_URL = "http://localhost:5000"  # Update if your API runs on a different host/port
DB_PATH = "instance/zo-sentinel.db"  # Update if your database is located elsewhere

def test_mcp_submission_integration():
    # Step 1: Prepare test data
    test_submission = {
        "title": "Test MCP Submission",
        "description": "This is a test submission for integration testing.",
        "author": "Integration Test User",
        "status": "draft"
    }

    # Step 2: Submit a new MCP via the API
    try:
        response = requests.post(
            f"{API_BASE_URL}/api/submissions",
            json=test_submission,
            headers={"Content-Type": "application/json"}
        )
        response.raise_for_status()  # Raises an HTTPError for bad responses (4XX, 5XX)

        if response.status_code not in (200, 201):
            print(f"FAIL: Submission failed with status code {response.status_code}")
            return

        submission_id = response.json().get("id")
        if not submission_id:
            print("FAIL: No submission ID returned")
            return

        print(f"Submission successful with ID: {submission_id}")

    except requests.exceptions.RequestException as e:
        print(f"FAIL: Error during submission - {str(e)}")
        return

    # Step 3: Retrieve the submitted MCP
    try:
        response = requests.get(
            f"{API_BASE_URL}/api/submissions/{submission_id}"
        )
        response.raise_for_status()

        if response.status_code != 200:
            print(f"FAIL: Retrieval failed with status code {response.status_code}")
            return

        retrieved_submission = response.json()

        # Step 4: Verify data integrity
        for key, value in test_submission.items():
            if retrieved_submission.get(key) != value:
                print(f"FAIL: Data mismatch for key '{key}'")
                print(f"Expected: {value}, Got: {retrieved_submission.get(key)}")
                return

        print("Data integrity verified")

    except requests.exceptions.RequestException as e:
        print(f"FAIL: Error during retrieval - {str(e)}")
        return

    # Step 5: Verify database update
    try:
        if not os.path.exists(DB_PATH):
            print(f"FAIL: Database not found at {DB_PATH}")
            return

        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()

        cursor.execute("SELECT COUNT(*) FROM mcp_submissions")
        count = cursor.fetchone()[0]

        if count == 0:
            print("FAIL: mcp_submissions table is empty")
            return

        print("mcp_submissions table verified")

    except sqlite3.Error as e:
        print(f"FAIL: Database error - {str(e)}")
        return
    finally:
        if 'conn' in locals():
            conn.close()

    print("PASS: All checks passed successfully")

if __name__ == "__main__":
    test_mcp_submission_integration()