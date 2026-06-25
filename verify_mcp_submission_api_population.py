import requests
import json

def verify_mcp_submission_api_population():
    # Define the test data
    test_data = {
        "mcp_id": "test_mcp_123",
        "submission_data": "test_data_123"
    }

    # Simulate a valid MCP submission via a POST request to the API
    api_url = "http://127.0.0.1:8772/mcp_submission"
    response = requests.post(api_url, json=test_data)

    if response.status_code != 200:
        print(f"API request failed with status code {response.status_code}")
        return False

    # Query the `mcp_submissions` table to assert that a new entry with the correct data has been created
    query_url = "http://127.0.0.1:8772/query"
    query_data = {
        "query": "SELECT * FROM mcp_submissions WHERE mcp_id = %s AND submission_data = %s",
        "params": [test_data["mcp_id"], test_data["submission_data"]]
    }
    response = requests.post(query_url, json=query_data)

    if response.status_code != 200:
        print(f"Database query failed with status code {response.status_code}")
        return False

    results = response.json()
    if not results:
        print("No matching entries found in the database")
        return False

    # Clean up the created entry
    cleanup_data = {
        "query": "DELETE FROM mcp_submissions WHERE mcp_id = %s AND submission_data = %s",
        "params": [test_data["mcp_id"], test_data["submission_data"]]
    }
    response = requests.post(query_url, json=cleanup_data)

    if response.status_code != 200:
        print(f"Database cleanup failed with status code {response.status_code}")
        return False

    return True

if __name__ == '__main__':
    if verify_mcp_submission_api_population():
        print('PASS')
    else:
        print('FAIL')