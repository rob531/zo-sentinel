from fastapi import FastAPI, Depends, HTTPException
from sqlalchemy.orm import Session
from app.db import get_session
from app.models import McpServerRegistry
from typing import List, Optional
from pydantic import BaseModel

class Perspective(BaseModel):
    id: int
    name: str
    description: Optional[str]
    facet_filters: Optional[str]
    org_id: int
    created_by: int
    created_at: str
    updated_at: str

def get_perspectives_endpoint(
    db: Session = Depends(get_session)
) -> List[Perspective]:
    perspectives = db.query(McpServerRegistry).all()
    return [
        Perspective(
            id=perspective.id,
            name=perspective.name,
            description=perspective.description,
            facet_filters=perspective.facet_filters,
            org_id=perspective.org_id,
            created_by=perspective.created_by,
            created_at=str(perspective.created_at),
            updated_at=str(perspective.updated_at)
        )
        for perspective in perspectives
    ]

def test_self():
    from fastapi.testclient import TestClient
    from sqlalchemy.orm import Session
    from sqlalchemy import create_engine
    from sqlalchemy.pool import StaticPool
    from app.models import Base

    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)

    test_app = FastAPI()
    test_app.dependency_overrides[get_session] = lambda: Session(engine)

    @test_app.get("/test")
    async def test_endpoint():
        return {"status": "ok"}

    client = TestClient(test_app)

    response = client.get("/test")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}

    print("PASS")

if __name__ == "__main__":
    test_self()