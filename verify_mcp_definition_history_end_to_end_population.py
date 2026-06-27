import requests
import json
import time
import uuid
from datetime import datetime, timezone, timedelta
from tenacity import retry, stop_after_attempt, wait_fixed, retry_if_exception_type
import logging

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# --- Configuration ---
# Base URL for the API server that handles database interactions.
# This should point to your backend service that exposes /api/db/insert and /api/db/query.
BASE_URL = "http://localhost:8000" 
INSERT_ENDPOINT = f"{BASE_URL}/api/db/insert"
QUERY_ENDPOINT = f"{BASE_URL}/api/db/query"

# Polling parameters for waiting for daemon processing
POLLING_INTERVAL_SECONDS = 5
POLLING_TIMEOUT_SECONDS = 60

# Retry parameters for API calls
RETRY_ATTEMPTS = 5
RETRY_WAIT_SECONDS = 2

# --- Helper Functions ---

@retry(
    stop=stop_after_attempt(RETRY_ATTEMPTS),
    wait=wait_fixed(RETRY_WAIT_SECONDS),
    retry=retry_if_exception_type(requests.exceptions.RequestException),
    reraise=True
)
def _make_api_request(url: str, payload: dict, method: str = 'POST', description: str = 'API call') -> dict:
    """
    Makes an API request with robust retry logic.
    Raises requests.exceptions.RequestException on failure after retries.
    """
    logger.info(f"Attempting {description} to {url} with payload: {json.dumps(payload)}")
    try:
        headers = {'Content-Type': 'application/json'}
        response = requests.request(method, url, data=json.dumps(payload), headers=headers, timeout=10)
        response.raise_for_status()  # Raise HTTPError for bad responses (4xx or 5xx)
        logger.info(f"Successful {description}. Status: {response.status_code}")
        return response.json()
    except requests.exceptions.RequestException as e:
        logger.warning(f"Failed {description}: {e}. Retrying...")
        raise

def generate_synthetic_mcp_submission():
    """
    Generates a unique synthetic MCP submission payload.
    Returns the submission data for `mcp_submissions` table and the expected
    parsed definition JSON for verification.
    """
    mcp_id = str(uuid.uuid4())
    version = "1.0.0" # For simplicity, using a fixed version for the first submission
    # Use UTC for consistency in timestamps
    submission_timestamp = datetime.now(timezone.utc).isoformat(timespec='milliseconds') + 'Z'

    definition_json = {
        "mcp_id": mcp_id,
        "version": version,
        "name": f"Test MCP {mcp_id[:8]}",
        "description": "A synthetic MCP definition for end-to-end testing.",
        "rules": [
            {"type": "rule_A", "value": "test_value_1"},
            {"type": "rule_B", "value": "test_value_2"}
        ],
        "metadata": {
            "author": "zo-sentinel-builder",
            "created_at": submission_timestamp
        }
    }

    # The `mcp_submissions` table stores `definition_json` as a string.
    # The `mcp_definition_history` table also stores it as a string.
    # We return the parsed dict for direct comparison.
    return {
        "mcp_id": mcp_id,
        "version": version,
        "definition_json": json.dumps(definition_json), # Store as JSON string in DB
        "submission_timestamp": submission_timestamp
    }, definition_json # Return both for verification

def main():
    logger.info("Starting MCP definition history end-to-end population verification script.")

    try:
        # 1. Generate and insert a synthetic MCP submission
        synthetic_submission_data, expected_definition_json = generate_synthetic_mcp_submission()
        mcp_id = synthetic_submission_data['mcp_id']
        version = synthetic_submission_data['version']
        submission_timestamp_str = synthetic_submission_data['submission_timestamp']
        # Convert submission timestamp to datetime object for comparison, handling 'Z' for UTC
        submission_datetime = datetime.fromisoformat(submission_timestamp_str.replace('Z', '+00:00'))

        logger.info(f"Generated synthetic MCP: mcp_id={mcp_id}, version={version}")

        insert_payload = {
            "table": "mcp_submissions",
            "data": synthetic_submission_data
        }
        _make_api_request(INSERT_ENDPOINT, insert_payload, description="inserting MCP submission")
        logger.info(f"Successfully inserted synthetic MCP submission into 'mcp_submissions'.")

        # 2. Wait for mcp_definition_history_populator_daemon.py to process
        logger.info(f"Waiting for daemon to process submission (mcp_id={mcp_id}, version={version}). Polling 'mcp_definition_history'...")
        start_time = time.time()
        found_in_history = False
        retrieved_entry = None

        while time.time() - start_time < POLLING_TIMEOUT_SECONDS:
            query_payload = {
                "table": "mcp_definition_history",
                "filter": {
                    "mcp_id": mcp_id,
                    "version": version
                }
            }
            try:
                response_data = _make_api_request(QUERY_ENDPOINT, query_payload, description="querying mcp_definition_history")
                results = response_data.get('results', [])

                if results:
                    logger.info(f"Found entry in 'mcp_definition_history' for mcp_id={mcp_id}, version={version}.")
                    retrieved_entry = results[0]
                    found_in_history = True
                    break
                else:
                    logger.info(f"Entry not yet found. Retrying in {POLLING_INTERVAL_SECONDS} seconds...")
            except requests.exceptions.RequestException as e:
                logger.warning(f"Query failed during polling: {e}. Will retry.")
            
            time.sleep(POLLING_INTERVAL_SECONDS)

        if not found_in_history:
            raise TimeoutError(f"Timed out after {POLLING_TIMEOUT_SECONDS} seconds waiting for MCP definition history entry.")

        # 3. Confirm presence and correctness of the new entry
        logger.info("Verifying correctness of the retrieved entry.")

        assert retrieved_entry is not None, "Retrieved entry should not be None."
        assert retrieved_entry.get('mcp_id') == mcp_id, \
            f"MCP ID mismatch: Expected {mcp_id}, Got {retrieved_entry.get('mcp_id')}"
        assert retrieved_entry.get('version') == version, \
            f"Version mismatch: Expected {version}, Got {retrieved_entry.get('version')}"

        retrieved_definition_json_str = retrieved_entry.get('definition_json')
        assert retrieved_definition_json_str is not None, "Retrieved 'definition_json' is None."
        retrieved_definition_json = json.loads(retrieved_definition_json_str)
        assert retrieved_definition_json == expected_definition_json, \
            f"Definition JSON mismatch: Expected {expected_definition_json}, Got {retrieved_definition_json}"

        # Verify effective_from timestamp (should be close to submission_timestamp)
        effective_from_str = retrieved_entry.get('effective_from')
        assert effective_from_str is not None, "Retrieved 'effective_from' is None."
        effective_from_datetime = datetime.fromisoformat(effective_from_str.replace('Z', '+00:00'))

        # Allow a small delta for processing time.
        # This accounts for network latency, daemon processing time, and polling interval.
        time_delta = abs(effective_from_datetime - submission_datetime)
        max_allowed_delta = timedelta(seconds=POLLING_INTERVAL_SECONDS + 10) # 10 seconds buffer for processing
        assert time_delta < max_allowed_delta, \
            f"Effective_from timestamp too far from submission timestamp. Delta: {time_delta}, Max allowed: {max_allowed_delta}"
        
        # Check effective_to (should typically be null or a very distant future date for the current active version)
        # Assuming 'null' or 'None' in Python for 'effective_to' for the current active version.
        # This might vary based on actual schema and daemon implementation.
        effective_to = retrieved_entry.get('effective_to')
        if effective_to is not None:
            # If the daemon sets it to a specific future date, this assertion might need adjustment.
            # For this test, we assume the latest version has a NULL effective_to.
            logger.warning(f"Retrieved 'effective_to' is not None: {effective_to}. "
                           "For the current active version, it's typically NULL. "
                           "If this is expected by your daemon, this warning can be ignored.")

        logger.info("MCP definition history end-to-end population verified successfully.")

    except (requests.exceptions.RequestException, AssertionError, TimeoutError) as e:
        logger.error(f"Verification failed: {e}")
        logger.error("MCP definition history end-to-end population verification FAILED.")
        exit(1)
    except Exception as e:
        logger.critical(f"An unexpected error occurred: {e}", exc_info=True)
        logger.error("MCP definition history end-to-end population verification FAILED due to unexpected error.")
        exit(1)

if __name__ == "__main__":
    main()