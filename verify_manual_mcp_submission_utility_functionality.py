import requests
import uuid
import json
from manual_mcp_submission_utility import submit_mcp_test_data

# Configuration
WRITE_SERVICE_URL = "http://write_service:8080"
MCP_SUBMISSIONS_ENDPOINT = f"{WRITE_SERVICE_URL}/mcp_submissions"

def verify_mcp_submission():
    # Generate unique test data
    test_data = {
        "id": str(uuid.uuid4()),
        "name": "Test MCP",
        "description": "Test description",
        "status": "submitted"
    }

    # Submit test data
    try:
        submit_mcp_test_data(test_data)
    except Exception as e:
        print(f"FAIL: Submission failed - {str(e)}")
        return

    # Verify submission
    try:
        response = requests.get(MCP_SUBMISSIONS_ENDPOINT)
        response.raise_for_status()
        submissions = response.json()

        # Check if our test record exists
        test_record_exists = any(
            submission["id"] == test_data["id"] and
            submission["name"] == test_data["name"] and
            submission["description"] == test_data["description"] and
            submission["status"] == test_data["status"]
            for submission in submissions
        )

        if not test_record_exists:
            print("FAIL: Test record not found in database")
            return

        # Clean up
        cleanup_response = requests.delete(f"{MCP_SUBMISSIONS_ENDPOINT}/{test_data['id']}")
        cleanup_response.raise_for_status()

        print("PASS")

    except requests.exceptions.RequestException as e:
        print(f"FAIL: Verification failed - {str(e)}")

if __name__ == "__main__":
    verify_mcp_submission()