import requests
import psycopg2
import os
import json
import uuid
from datetime import datetime, timezone

# --- Configuration ---
# API Endpoint for MCP submissions
API_BASE_URL = os.getenv("API_BASE_URL", "http://localhost:8000")
SUBMIT_MCP_ENDPOINT = f"{API_BASE_URL}/submit_mcp"

# Database connection details for verification
DB_HOST = os.getenv("DB_HOST", "localhost")
DB_PORT = os.getenv("DB_PORT", "5432")
DB_NAME = os.getenv("DB_NAME", "zo_sentinel_db")
DB_USER = os.getenv("DB_USER", "admin")
DB_PASSWORD = os.getenv("DB_PASSWORD", "password")

# Database table name where MCP submissions are stored
MCP_SUBMISSIONS_TABLE = "mcp_submissions"

# --- Helper Functions ---
def get_db_connection():
    """Establishes and returns a database connection."""
    try:
        conn = psycopg2.connect(
            host=DB_HOST,
            port=DB_PORT,
            database=DB_NAME,
            user=DB_USER,
            password=DB_PASSWORD
        )
        return conn
    except psycopg2.Error as e:
        print(f"ERROR: Could not connect to the database: {e}")
        raise

def generate_mcp_payload(mcp_id: str):
    """Generates a valid MCP payload for submission."""
    now_utc = datetime.now(timezone.utc).isoformat(timespec='seconds').replace('+00:00', 'Z')
    return {
        "mcp_id": mcp_id,
        "submission_date": now_utc,
        "status": "PENDING",
        "data": {
            "source": "test_script",
            "version": "1.0",
            "metadata": {
                "test_run_id": str(uuid.uuid4()),
                "timestamp": now_utc
            },
            "content": {
                "type": "test_mcp",
                "description": f"Test MCP submission from verify_mcp_submissions_api_functionality.py for {mcp_id}"
            }
        }
    }

def verify_mcp_submission_in_db(mcp_id: str, expected_payload: dict) -> bool:
    """
    Queries the database to confirm the MCP submission exists and matches.
    Assumes the table has at least 'mcp_id' and 'payload' (jsonb) columns.
    """
    conn = None
    try:
        conn = get_db_connection()
        cur = conn.cursor()

        # Query for the submitted MCP
        query = f"SELECT mcp_id, payload FROM {MCP_SUBMISSIONS_TABLE} WHERE mcp_id = %s;"
        cur.execute(query, (mcp_id,))
        result = cur.fetchone()

        if result:
            db_mcp_id, db_payload = result
            print(f"DEBUG: Found record in DB for mcp_id: {db_mcp_id}")
            # Basic verification: check mcp_id and a key from the payload
            if db_mcp_id == mcp_id and db_payload.get("data", {}).get("source") == expected_payload["data"]["source"]:
                print("DEBUG: Database record matches expected payload (partial check).")
                return True
            else:
                print(f"ERROR: Database record found but data mismatch for mcp_id: {mcp_id}")
                print(f"  Expected payload (partial): {expected_payload['data']['source']}")
                print(f"  Actual DB payload (partial): {db_payload.get('data', {}).get('source')}")
                return False
        else:
            print(f"ERROR: No record found in {MCP_SUBMISSIONS_TABLE} for mcp_id: {mcp_id}")
            return False
    except Exception as e:
        print(f"ERROR: Database verification failed: {e}")
        return False
    finally:
        if conn:
            conn.close()

def cleanup_mcp_submission(mcp_id: str):
    """Deletes the test MCP submission from the database."""
    conn = None
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        query = f"DELETE FROM {MCP_SUBMISSIONS_TABLE} WHERE mcp_id = %s;"
        cur.execute(query, (mcp_id,))
        conn.commit()
        print(f"DEBUG: Cleaned up test record for mcp_id: {mcp_id}")
    except Exception as e:
        print(f"WARNING: Failed to clean up test record for mcp_id {mcp_id}: {e}")
    finally:
        if conn:
            conn.close()

# --- Main Test Logic ---
def run_test():
    """
    Executes the end-to-end test for MCP submission API functionality.
    """
    test_mcp_id = f"test-mcp-{uuid.uuid4()}"
    mcp_payload = generate_mcp_payload(test_mcp_id)
    
    print(f"--- Starting MCP Submission API Functionality Test ---")
    print(f"Target API: {SUBMIT_MCP_ENDPOINT}")
    print(f"Target DB Table: {MCP_SUBMISSIONS_TABLE}")
    print(f"Generated Test MCP ID: {test_mcp_id}")

    try:
        # 1. Submit MCP via API
        print(f"\nSTEP 1: Submitting MCP with ID '{test_mcp_id}' to {SUBMIT_MCP_ENDPOINT}...")
        response = requests.post(SUBMIT_MCP_ENDPOINT, json=mcp_payload)
        response.raise_for_status()  # Raise an exception for HTTP errors (4xx or 5xx)

        print(f"API Response Status Code: {response.status_code}")
        print(f"API Response Body: {response.json()}")
        assert response.status_code in [200, 201], f"Expected status code 200 or 201, got {response.status_code}"
        assert "message" in response.json(), "API response missing 'message' key"
        print(f"SUCCESS: MCP '{test_mcp_id}' submitted successfully via API.")

        # 2. Verify MCP in Database
        print(f"\nSTEP 2: Verifying MCP '{test_mcp_id}' in database...")
        # Give a small buffer for async processing if any, though direct DB check assumes sync write.
        # time.sleep(1) 
        is_verified = verify_mcp_submission_in_db(test_mcp_id, mcp_payload)
        assert is_verified, f"MCP '{test_mcp_id}' not found or data mismatch in database."
        print(f"SUCCESS: MCP '{test_mcp_id}' successfully verified in the database.")

        print(f"\n--- Test PASSED ---")
        return True

    except requests.exceptions.ConnectionError:
        print(f"ERROR: Could not connect to the API at {API_BASE_URL}. Is the API service running?")
        return False
    except requests.exceptions.RequestException as e:
        print(f"ERROR: API request failed: {e}")
        if hasattr(e, 'response') and e.response is not None:
            print(f"API Error Response: {e.response.text}")
        return False
    except AssertionError as e:
        print(f"FAIL: Assertion failed - {e}")
        return False
    except Exception as e:
        print(f"AN UNEXPECTED ERROR OCCURRED: {e}")
        return False
    finally:
        # Cleanup the test data regardless of test outcome
        print(f"\nSTEP 3: Cleaning up test data for MCP ID '{test_mcp_id}'...")
        cleanup_mcp_submission(test_mcp_id)
        print(f"--- Test Finished ---")


if __name__ == '__main__':
    # Set environment variables for testing if not already set
    # Example:
    # os.environ['API_BASE_URL'] = 'http://localhost:8000'
    # os.environ['DB_HOST'] = 'localhost'
    # os.environ['DB_PORT'] = '5432'
    # os.environ['DB_NAME'] = 'zo_sentinel_db'
    # os.environ['DB_USER'] = 'admin'
    # os.environ['DB_PASSWORD'] = 'password'

    if run_test():
        print("PASS")
        exit(0)
    else:
        print("FAIL")
        exit(1)