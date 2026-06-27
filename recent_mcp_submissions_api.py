from fastapi import FastAPI, Query, HTTPException
from pydantic import BaseModel
import requests
import httpx
import uvicorn
from typing import List, Optional

app = FastAPI()

class MCPSubmission(BaseModel):
    id: int
    title: str
    author: str
    submission_timestamp: str
    status: str

class PaginatedSubmissions(BaseModel):
    submissions: List[MCPSubmission]
    total: int
    limit: int
    offset: int

@app.get("/submissions/recent", response_model=PaginatedSubmissions)
async def get_recent_submissions(
    limit: int = Query(10, ge=1, le=100),
    offset: int = Query(0, ge=0)
):
    try:
        query = {
            "query": f"""
                SELECT id, title, author, submission_timestamp, status
                FROM mcp_submissions
                ORDER BY submission_timestamp DESC
                LIMIT {limit} OFFSET {offset}
            """,
            "variables": {}
        }

        response = requests.post("http://127.0.0.1:8772/query", json=query)
        response.raise_for_status()
        data = response.json()

        if "data" not in data or not data["data"]:
            raise HTTPException(status_code=404, detail="No submissions found")

        submissions = [
            MCPSubmission(
                id=row[0],
                title=row[1],
                author=row[2],
                submission_timestamp=row[3],
                status=row[4]
            ) for row in data["data"]
        ]

        total_query = {
            "query": "SELECT COUNT(*) FROM mcp_submissions",
            "variables": {}
        }

        total_response = requests.post("http://127.0.0.1:8772/query", json=total_query)
        total_response.raise_for_status()
        total_data = total_response.json()
        total = total_data["data"][0][0] if "data" in total_data and total_data["data"] else 0

        return {
            "submissions": submissions,
            "total": total,
            "limit": limit,
            "offset": offset
        }

    except requests.exceptions.RequestException as e:
        raise HTTPException(status_code=500, detail=f"Error querying database: {str(e)}")

if __name__ == "__main__":
    import threading

    def test_api():
        with httpx.Client() as client:
            response = client.get("http://127.0.0.1:8000/submissions/recent?limit=5&offset=0")
            assert response.status_code == 200
            data = response.json()
            assert isinstance(data["submissions"], list)
            assert len(data["submissions"]) <= 5
            assert data["limit"] == 5
            assert data["offset"] == 0

            # Check if submissions are ordered by timestamp
            timestamps = [sub["submission_timestamp"] for sub in data["submissions"]]
            assert timestamps == sorted(timestamps, reverse=True)

            print("PASS")

    # Start the FastAPI app in a separate thread
    threading.Thread(target=uvicorn.run, args=(app, {"host": "127.0.0.1", "port": 8000})).start()

    # Run the test
    test_api()