from fastapi import APIRouter, Depends, HTTPException
from fastapi.testclient import TestClient
from pydantic import BaseModel
from typing import List, Optional
import sqlite3

router = APIRouter()

class ServerRegistry(BaseModel):
    server_id: str
    trust_score: float
    # Add other fields as needed

def get_db():
    conn = sqlite3.connect('mcp_server_registry.db')
    try:
        yield conn
    finally:
        conn.close()

@router.get("/mcp/server_registry", response_model=List[ServerRegistry])
async def list_server_registry(server_id: Optional[str] = None, trust_score: Optional[float] = None, db: sqlite3.Connection = Depends(get_db)):
    query = "SELECT server_id, trust_score FROM mcp_server_registry"
    params = []

    if server_id is not None or trust_score is not None:
        query += " WHERE"
        conditions = []
        if server_id is not None:
            conditions.append(" server_id = ?")
            params.append(server_id)
        if trust_score is not None:
            conditions.append(" trust_score = ?")
            params.append(trust_score)
        query += " AND".join(conditions)

    cursor = db.cursor()
    cursor.execute(query, params)
    rows = cursor.fetchall()

    server_registry = [ServerRegistry(server_id=row[0], trust_score=row[1]) for row in rows]
    return server_registry

if __name__ == "__main__":
    from fastapi import FastAPI

    app = FastAPI()
    app.include_router(router)

    client = TestClient(app)

    # Test the /mcp/server_registry endpoint
    response = client.get("/mcp/server_registry")
    assert response.status_code == 200
    assert isinstance(response.json(), list)

    # Test filtering by server_id
    response = client.get("/mcp/server_registry?server_id=test_server")
    assert response.status_code == 200
    assert isinstance(response.json(), list)

    # Test filtering by trust_score
    response = client.get("/mcp/server_registry?trust_score=0.9")
    assert response.status_code == 200
    assert isinstance(response.json(), list)

    # Test filtering by both server_id and trust_score
    response = client.get("/mcp/server_registry?server_id=test_server&trust_score=0.9")
    assert response.status_code == 200
    assert isinstance(response.json(), list)