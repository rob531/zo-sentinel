import requests
import json
import os
import datetime
import uuid

# --- Configuration ---
# The URL of the write service endpoint to test.
# Defaults to http://localhost:8000/write, can be overridden by WRITE_SERVICE_URL environment variable.
WRITE_SERVICE_URL = os.getenv("WRITE_SERVICE_URL", "http://localhost:8000/write")

# Timeout for the HTTP request in seconds.
# Defaults to 10 seconds, can be overridden by WRITE_SERVICE_TIMEOUT environment variable.
TIMEOUT_SECONDS = int(os.getenv("WRITE_SERVICE_TIMEOUT", "10"))

# The name of the table to write test data to.
# Using 'service_health' as suggested, which is typically non-critical.
# Can be overridden by WRITE_SERVICE_TEST_TABLE environment variable.
TEST_TABLE_NAME = os.getenv("WRITE_SERVICE_TEST_TABLE", "service_health")

def run_diagnostic():
    """
    Runs a diagnostic test for the write_service by attempting a write operation.
    It reports success or failure, including detailed error information if applicable.
    """
    print(f"--- Starting write service write request diagnostic ---")
    print(f"Target Write Service URL: {WRITE_SERVICE_URL}")
    print(f"Request Timeout: {TIMEOUT_SECONDS} seconds")
    print(f"Test Table Name: {TEST_TABLE_NAME}")

    # Generate unique test data to avoid conflicts and identify diagnostic entries.
    test_service_name = f"diagnostic_test_{uuid.uuid4().hex[:12]}"
    timestamp = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec='seconds') + 'Z' # ISO 8601 with Z for UTC

    # Construct the test payload.
    # This structure assumes a simple JSON payload for a write service,
    # where 'table' specifies the target table and 'rows' is a list of dictionaries
    # representing the data to be inserted.
    test_data = {
        "table": TEST_TABLE_NAME,
        "rows": [
            {
                "service": test_service_name,
                "status": "healthy",
                "last_check": timestamp,
                "diagnostic_run_id": str(uuid.uuid4()),
                "message": "Automated diagnostic write test"
            }
        ]
    }

    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json"
    }

    print(f"\nAttempting to write test data to table '{TEST_TABLE_NAME}'...")
    print(f"Test Service Name: '{test_service_name}'")
    print(f"Request Payload Preview: {json.dumps(test_data, indent=2)[:200]}...") # Show a snippet

    try:
        # Send the POST request to the write service
        response = requests.post(
            WRITE_SERVICE_URL,
            data=json.dumps(test_data),
            headers=headers,
            timeout=TIMEOUT_SECONDS
        )

        # Check the HTTP response status code
        if response.status_code == 200:
            print("\nSUCCESS: Write service successfully processed the request.")
            print(f"HTTP Status Code: {response.status_code}")
            try:
                response_json = response.json()
                print(f"Response Body: {json.dumps(response_json, indent=2)}")
            except json.JSONDecodeError:
                print(f"Response Body (non-JSON): {response.text}")
        else:
            print(f"\nFAILURE: Write service returned an error status code: {response.status_code}")
            print(f"HTTP Status Code: {response.status_code}")
            try:
                error_details = response.json()
                print(f"Error Details from Response: {json.dumps(error_details, indent=2)}")
            except json.JSONDecodeError:
                print(f"Error Response Body (non-JSON): {response.text}")
            print(f"Full Request Payload Sent: {json.dumps(test_data, indent=2)}")

    except requests.exceptions.Timeout:
        print(f"\nFAILURE: Request to write service timed out after {TIMEOUT_SECONDS} seconds.")
        print(f"Target URL: {WRITE_SERVICE_URL}")
        print(f"Full Request Payload Sent: {json.dumps(test_data, indent=2)}")
    except requests.exceptions.ConnectionError as e:
        print(f"\nFAILURE: Could not connect to write service at {WRITE_SERVICE_URL}.")
        print(f"Error Details: {e}")
        print(f"Full Request Payload Sent: {json.dumps(test_data, indent=2)}")
    except requests.exceptions.RequestException as e:
        print(f"\nFAILURE: An unexpected request error occurred: {e}")
        print(f"Target URL: {WRITE_SERVICE_URL}")
        print(f"Full Request Payload Sent: {json.dumps(test_data, indent=2)}")
    except Exception as e:
        print(f"\nFAILURE: An unexpected error occurred during diagnostic: {e}")
        print(f"Target URL: {WRITE_SERVICE_URL}")
        print(f"Full Request Payload Sent: {json.dumps(test_data, indent=2)}")
    finally:
        print('\nWrite service write request diagnostic complete.')

if __name__ == "__main__":
    run_diagnostic()