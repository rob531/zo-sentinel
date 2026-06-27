from fastapi import FastAPI, HTTPException, status
from fastapi.testclient import TestClient
from pydantic import BaseModel
import requests
from typing import Optional

app = FastAPI()

class MCPSubmissionDetail(BaseModel):
    submission_id: int
    title: str
    description: str
    status: str
    created_at: str
    updated_at: str
    author_id: int
    author_name: str
    review_comments: Optional[str] = None
    is_public: bool

@app.get("/mcp_submissions/{submission_id}/detail", response_model=MCPSubmissionDetail)
async def get_mcp_submission_detail(submission_id: int):
    query = """
    SELECT
        s.submission_id,
        s.title,
        s.description,
        s.status,
        s.created_at,
        s.updated_at,
        s.author_id,
        u.username AS author_name,
        s.review_comments,
        s.is_public
    FROM
        mcp_submissions s
    JOIN
        users u ON s.author_id = u.user_id
    WHERE
        s.submission_id = %s
    """
    try:
        response = requests.post(
            "http://127.0.0.1:8772/query",
            json={"query": query, "params": [submission_id]}
        )
        response.raise_for_status()
        result = response.json()

        if not result:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="MCP submission not found"
            )

        return result[0]
    except requests.exceptions.RequestException as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Database query failed: {str(e)}"
        )

if __name__ == "__main__":
    # Seed in-memory store for testing
    test_data = {
        "submission_id": 1,
        "title": "Test Submission",
        "description": "This is a test submission",
        "status": "draft",
        "created_at": "2023-01-01T00:00:00",
        "updated_at": "2023-01-01T00:00:00",
        "author_id": 1,
        "author_name": "test_user",
        "review_comments": None,
        "is_public": False
    }

    # Mock the database response
    def mock_post(url, json):
        if json["params"][0] == 1:
            return requests.Response()
        return requests.Response()

    requests.post = mock_post

    client = TestClient(app)

    # Test valid submission
    response = client.get("/mcp_submissions/1/detail")
    assert response.status_code == 200
    assert response.json() == test_data

    # Test invalid submission
    response = client.get("/mcp_submissions/999/detail")
    assert response.status_code == 404

    print("PASS")