from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import requests
from typing import List, Optional
import json
from fastapi.testclient import TestClient

app = FastAPI()

class Submission(BaseModel):
    mcp_name: str
    requested_by: str
    status: str
    submission_timestamp: str

@app.get("/admin/submissions", response_model=List[Submission])
async def get_submissions():
    try:
        # Query the database via the write_service
        response = requests.post(
            "http://127.0.0.1:8772/query",
            json={
                "query": "SELECT mcp_name, requested_by, status, submission_timestamp FROM mcp_submissions"
            }
        )
        response.raise_for_status()
        data = response.json()

        # Check if the response contains data
        if "data" in data and data["data"]:
            return data["data"]
        else:
            return []

    except requests.exceptions.RequestException as e:
        raise HTTPException(status_code=500, detail=f"Database query failed: {str(e)}")

if __name__ == "__main__":
    client = TestClient(app)

    # Test the endpoint
    response = client.get("/admin/submissions")

    # Assert the response status code and content type
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/json"

    # Try to parse the response as JSON
    try:
        json_response = response.json()
        assert isinstance(json_response, list)
        print("PASS")
    except json.JSONDecodeError:
        print("FAIL: Invalid JSON response")