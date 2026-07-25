from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import List
from sqlalchemy.orm import Session
from app.db import get_session
from app.models import MCPServerRegistry

router = APIRouter()

class UncalledRouterResponse(BaseModel):
    uncalled_routers: List[str]

def get_routing_registry(db: Session = Depends(get_session)):
    return db.query(MCPServerRegistry).all()

def get_call_graph(db: Session = Depends(get_session)):
    # In a real implementation, this would query the call graph from the database
    # For this example, we'll return a mock call graph
    return {
        "router1": ["router2"],
        "router2": ["router3"],
        "router3": []
    }

@router.get("/probes/routers/callers", response_model=UncalledRouterResponse)
async def get_uncalled_routers(db: Session = Depends(get_session)):
    routing_registry = get_routing_registry(db)
    call_graph = get_call_graph(db)

    # Get all router names from the routing registry
    all_routers = {router.name for router in routing_registry}

    # Get all called router names from the call graph
    called_routers = set()
    for caller, callees in call_graph.items():
        called_routers.update(callees)

    # Find uncalled routers
    uncalled_routers = list(all_routers - called_routers)

    return {"uncalled_routers": uncalled_routers}

if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from app.db import Base, engine
    from app.models import MCPServerRegistry

    # Create a test database
    Base.metadata.create_all(bind=engine)

    # Add test data
    test_routers = [
        MCPServerRegistry(name="router1"),
        MCPServerRegistry(name="router2"),
        MCPServerRegistry(name="router3"),
        MCPServerRegistry(name="router4"),
    ]

    from app.db import SessionLocal
    db = SessionLocal()
    db.add_all(test_routers)
    db.commit()
    db.close()

    # Create a test client
    from fastapi import FastAPI
    app = FastAPI()
    app.include_router(router)

    client = TestClient(app)

    # Test the endpoint
    response = client.get("/probes/routers/callers")
    assert response.status_code == 200
    assert "uncalled_routers" in response.json()
    assert len(response.json()["uncalled_routers"]) >= 1

    print("PASS")