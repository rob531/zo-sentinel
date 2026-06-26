import requests
import json
from typing import List, Dict

def check_table_empty() -> bool:
    """Check if mcp_submissions table is empty."""
    response = requests.get("http://127.0.0.1:8772/query", params={"q": "SELECT COUNT(*) FROM mcp_submissions"})
    if response.status_code != 200:
        raise Exception(f"Failed to query mcp_submissions: {response.text}")
    count = response.json()["results"][0]["series"][0]["values"][0][1]
    return count == 0

def populate_mcp_submissions() -> None:
    """Populate mcp_submissions table with synthetic test data."""
    submissions = [
        {
            "measurement": "mcp_submissions",
            "tags": {"submission_id": "sub1", "user_id": "user1", "status": "pending"},
            "fields": {"submission_date": "2023-01-01T00:00:00Z", "data_size": 1024},
            "time": "2023-01-01T00:00:00Z"
        },
        {
            "measurement": "mcp_submissions",
            "tags": {"submission_id": "sub2", "user_id": "user2", "status": "completed"},
            "fields": {"submission_date": "2023-01-02T00:00:00Z", "data_size": 2048},
            "time": "2023-01-02T00:00:00Z"
        },
        {
            "measurement": "mcp_submissions",
            "tags": {"submission_id": "sub3", "user_id": "user3", "status": "pending"},
            "fields": {"submission_date": "2023-01-03T00:00:00Z", "data_size": 512},
            "time": "2023-01-03T00:00:00Z"
        },
        {
            "measurement": "mcp_submissions",
            "tags": {"submission_id": "sub4", "user_id": "user4", "status": "failed"},
            "fields": {"submission_date": "2023-01-04T00:00:00Z", "data_size": 4096},
            "time": "2023-01-04T00:00:00Z"
        },
        {
            "measurement": "mcp_submissions",
            "tags": {"submission_id": "sub5", "user_id": "user5", "status": "completed"},
            "fields": {"submission_date": "2023-01-05T00:00:00Z", "data_size": 2048},
            "time": "2023-01-05T00:00:00Z"
        }
    ]

    for submission in submissions:
        response = requests.post("http://127.0.0.1:8772/write", data=json.dumps(submission))
        if response.status_code != 204:
            raise Exception(f"Failed to insert submission: {response.text}")

def run() -> None:
    """Main function to check and populate mcp_submissions table."""
    if check_table_empty():
        print("mcp_submissions table is empty. Populating with test data...")
        populate_mcp_submissions()
    else:
        print("mcp_submissions table is not empty. Skipping population.")

    # Verify the table is no longer empty
    response = requests.get("http://127.0.0.1:8772/query", params={"q": "SELECT COUNT(*) FROM mcp_submissions"})
    if response.status_code != 200:
        raise Exception(f"Failed to query mcp_submissions: {response.text}")
    count = response.json()["results"][0]["series"][0]["values"][0][1]
    print(f"mcp_submissions table contains {count} records.")
    if count > 0:
        print("PASS")

if __name__ == "__main__":
    run()