from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
import requests

router = APIRouter()

class SavedSearch(BaseModel):
    user_id: int
    search_query: str

@router.post("/saved_searches")
async def save_search(saved_search: SavedSearch):
    data = {
        "table": "mcp_user_saved_searches",
        "data": {
            "user_id": saved_search.user_id,
            "search_query": saved_search.search_query
        }
    }
    response = requests.post("http://write_service/write", json=data)
    if response.status_code != 200:
        raise HTTPException(status_code=500, detail="Failed to save search")
    return {"message": "Search saved successfully"}

@router.get("/saved_searches/{user_id}")
async def get_saved_searches(user_id: int):
    data = {
        "table": "mcp_user_saved_searches",
        "query": {"user_id": user_id}
    }
    response = requests.post("http://write_service/read", json=data)
    if response.status_code != 200:
        raise HTTPException(status_code=500, detail="Failed to retrieve searches")
    return response.json()

@router.delete("/saved_searches/{search_id}")
async def delete_saved_search(search_id: int):
    data = {
        "table": "mcp_user_saved_searches",
        "query": {"id": search_id}
    }
    response = requests.post("http://write_service/delete", json=data)
    if response.status_code != 200:
        raise HTTPException(status_code=500, detail="Failed to delete search")
    return {"message": "Search deleted successfully"}

if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    app = FastAPI()
    app.include_router(router)

    client = TestClient(app)

    # Test saving a search
    response = client.post("/saved_searches", json={"user_id": 1, "search_query": "test query"})
    assert response.status_code == 200
    assert response.json() == {"message": "Search saved successfully"}

    # Test retrieving saved searches
    response = client.get("/saved_searches/1")
    assert response.status_code == 200
    searches = response.json()
    assert len(searches) == 1
    assert searches[0]["user_id"] == 1
    assert searches[0]["search_query"] == "test query"

    # Test deleting a saved search
    search_id = searches[0]["id"]
    response = client.delete(f"/saved_searches/{search_id}")
    assert response.status_code == 200
    assert response.json() == {"message": "Search deleted successfully"}

    # Verify search is deleted
    response = client.get("/saved_searches/1")
    assert response.status_code == 200
    assert len(response.json()) == 0

    print("PASS")