from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from typing import List, Optional
import requests
from app.db import get_session
from app.models import MCPServerRegistry, MCPLLMAxisScores
from sqlalchemy.orm import Session
from sqlalchemy import func

router = APIRouter()

class Server(BaseModel):
    server_id: int
    name: str
    registry_source: str
    first_seen: str
    url: str

class UnscoredServersResponse(BaseModel):
    total: int
    servers: List[Server]

@router.get("/servers/unscored", response_model=UnscoredServersResponse)
async def get_unscored_servers(
    limit: int = Query(50, le=500),
    offset: int = Query(0),
    registry_source: Optional[str] = None,
    session: Session = Depends(get_session)
):
    query = session.query(MCPServerRegistry).\
        outerjoin(MCPLLMAxisScores, MCPServerRegistry.server_id == MCPLLMAxisScores.server_id).\
        filter(MCPLLMAxisScores.id == None)

    if registry_source:
        query = query.filter(MCPServerRegistry.registry_source == registry_source)

    total = query.count()
    servers = query.limit(limit).offset(offset).all()

    return {
        "total": total,
        "servers": [
            {
                "server_id": server.server_id,
                "name": server.name,
                "registry_source": server.registry_source,
                "first_seen": server.first_seen.isoformat(),
                "url": server.url
            } for server in servers
        ]
    }

if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from app.main import app
    from unittest.mock import patch

    mock_servers = [
        {"server_id": 1, "name": "Server 1", "registry_source": "source1", "first_seen": "2023-01-01T00:00:00", "url": "http://server1.com"},
        {"server_id": 2, "name": "Server 2", "registry_source": "source2", "first_seen": "2023-01-02T00:00:00", "url": "http://server2.com"},
        {"server_id": 3, "name": "Server 3", "registry_source": "source1", "first_seen": "2023-01-03T00:00:00", "url": "http://server3.com"},
    ]

    with patch('requests.post') as mock_post:
        mock_post.return_value.json.return_value = {"rows": []}

        client = TestClient(app)

        # Test without filter
        response = client.get("/servers/unscored")
        assert response.status_code == 200
        assert "total" in response.json()
        assert isinstance(response.json()["servers"], list)

        # Test with filter
        response = client.get("/servers/unscored?registry_source=source1")
        assert response.status_code == 200
        assert "total" in response.json()
        assert isinstance(response.json()["servers"], list)
        assert all(server["registry_source"] == "source1" for server in response.json()["servers"])

        print("PASS")