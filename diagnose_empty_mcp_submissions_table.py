import requests
import logging

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

def query_mcp_submissions_table():
    """Query the mcp_submissions table via the write_service query endpoint."""
    url = "http://localhost:8000/write_service/query"
    query = "SELECT * FROM mcp_submissions;"
    payload = {"query": query}
    headers = {"Content-Type": "application/json"}

    try:
        response = requests.post(url, json=payload, headers=headers)
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        logging.error(f"Error querying mcp_submissions table: {e}")
        return None

def make_dummy_submission():
    """Make a dummy submission through the mcp_submission_api.py."""
    url = "http://localhost:8000/mcp_submission_api/submit"
    dummy_data = {
        "client_id": "dummy_client",
        "submission_data": {"key": "value"}
    }
    headers = {"Content-Type": "application/json"}

    try:
        response = requests.post(url, json=dummy_data, headers=headers)
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        logging.error(f"Error making dummy submission: {e}")
        return None

def diagnose_empty_mcp_submissions_table():
    """Diagnose why the mcp_submissions table remains empty."""
    logging.info("Starting diagnosis of empty mcp_submissions table...")

    # Query the mcp_submissions table initially
    initial_query_result = query_mcp_submissions_table()
    if initial_query_result is None:
        logging.error("Initial query of mcp_submissions table failed. Aborting diagnosis.")
        return False

    initial_count = len(initial_query_result.get("data", []))
    logging.info(f"Initial count of entries in mcp_submissions table: {initial_count}")

    if initial_count > 0:
        logging.info("mcp_submissions table is not empty. No further diagnosis needed.")
        return True

    # Make a dummy submission
    submission_result = make_dummy_submission()
    if submission_result is None:
        logging.error("Dummy submission failed. Aborting diagnosis.")
        return False

    logging.info("Dummy submission successful. Re-querying mcp_submissions table...")

    # Re-query the mcp_submissions table
    final_query_result = query_mcp_submissions_table()
    if final_query_result is None:
        logging.error("Final query of mcp_submissions table failed. Aborting diagnosis.")
        return False

    final_count = len(final_query_result.get("data", []))
    logging.info(f"Final count of entries in mcp_submissions table: {final_count}")

    if final_count > initial_count:
        logging.info("mcp_submissions table was successfully populated with the dummy submission.")
        return True
    else:
        logging.error("mcp_submissions table remains empty after dummy submission.")
        return False

if __name__ == '__main__':
    success = diagnose_empty_mcp_submissions_table()
    if success:
        print("PASS: mcp_submissions table diagnosis completed successfully.")
    else:
        print("FAIL: mcp_submissions table diagnosis encountered issues.")