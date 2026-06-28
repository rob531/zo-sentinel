from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
import requests
from typing import List

router = APIRouter()

class User(BaseModel):
    id: int
    email: str
    role: str

@router.get("/orgs/{org_id}/users", response_model=List[User])
async def get_org_users(org_id: int):
    query = "SELECT id, email, role FROM users WHERE org_id = %s"
    params = (org_id,)
    response = requests.post("http://127.0.0.1:8772/query", json={"query": query, "params": params})
    if response.status_code != 200:
        raise HTTPException(status_code=response.status_code, detail="Error querying write_service")
    return response.json()

if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from fastapi import FastAPI
    import pytest

    app = FastAPI()
    app.include_router(router)

    client = TestClient(app)

    def test_get_org_users():
        org_id = 1
        expected_users = [
            {"id": 1, "email": "user1@example.com", "role": "admin"},
            {"id": 2, "email": "user2@example.com", "role": "member"}
        ]

        def mock_post(url, json, **kwargs):
            class MockResponse:
                def __init__(self, json_data, status_code):
                    self.json_data = json_data
                    self.status_code = status_code

                def json(self):
                    return self.json_data

            if url == "http://127.0.0.1:8772/query" and json["query"] == "SELECT id, email, role FROM users WHERE org_id = %s" and json["params"] == (org_id,):
                return MockResponse(expected_users, 200)
            else:
                return MockResponse(None, 404)

        requests.post = mock_post

        response = client.get(f"/orgs/{org_id}/users")
        assert response.status_code == 200
        assert response.json() == expected_users
        print("PASS")

    test_get_org_users()