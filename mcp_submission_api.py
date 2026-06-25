from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import requests
from typing import Dict, Any

app = FastAPI()

class MCPSubmission(BaseModel):
    mcp_name: str
    requested_by: str
    mcp_definition_json: Dict[str, Any]

@app.post("/mcp/submit")
async def submit_mcp(submission: MCPSubmission):
    try:
        # Prepare the data for the write_service
        data = {
            "table": "mcp_submissions",
            "data": {
                "mcp_name": submission.mcp_name,
                "requested_by": submission.requested_by,
                "mcp_definition_json": submission.mcp_definition_json,
                "submission_time": "NOW()"  # Assuming the DB handles this as a timestamp
            }
        }

        # Send the data to the write_service
        response = requests.post(
            "http://127.0.0.1:8772/write",
            json=data,
            headers={"Content-Type": "application/json"}
        )

        if response.status_code == 200:
            return {"status": "success", "message": "MCP submitted successfully"}
        else:
            raise HTTPException(status_code=response.status_code, detail="Failed to submit MCP")

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

if __name__ == "__main__":
    import uvicorn
    from fastapi.testclient import TestClient

    client = TestClient(app)

    # Test data
    test_data = {
        "mcp_name": "Test MCP",
        "requested_by": "test_user",
        "mcp_definition_json": {"key": "value"}
    }

    # Mock the write_service response
    def mock_post(*args, **kwargs):
        return requests.Response()
    requests.post = mock_post

    # Send the test request
    response = client.post("/mcp/submit", json=test_data)

    # Assert the response
    assert response.status_code == 200
    assert response.json() == {"status": "success", "message": "MCP submitted successfully"}

    # Print PASS if all assertions pass
    print("PASS")