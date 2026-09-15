from fastapi import FastAPI, Depends, HTTPException
from sqlalchemy.orm import Session
from app.db import get_session
from app.models import Perspective
from typing import Optional
from pydantic import BaseModel

class PerspectiveQueryResponse(BaseModel):
    id: int
    name: str
    description: Optional[str]
    facet_filters: Optional[str]
    created_at: str
    updated_at: str
    created_by: int
    org_id: int

app = FastAPI()

@app.get("/api/perspectives/{perspective_id}/query", response_model=PerspectiveQueryResponse)
async def get_perspective_query(
    perspective_id: int,
    session: Session = Depends(get_session)
):
    perspective = session.query(Perspective).filter(Perspective.id == perspective_id).first()
    if not perspective:
        raise HTTPException(status_code=404, detail="Perspective not found")
    return {
        "id": perspective.id,
        "name": perspective.name,
        "description": perspective.description,
        "facet_filters": perspective.facet_filters,
        "created_at": str(perspective.created_at),
        "updated_at": str(perspective.updated_at),
        "created_by": perspective.created_by,
        "org_id": perspective.org_id
    }

if __name__ == "__main__":
    from sqlalchemy.pool import StaticPool
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    # Set up in-memory SQLite for self-test
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    # Create tables
    from app.models import Base
    Base.metadata.create_all(bind=engine)

    # Override dependency
    app.dependency_overrides[get_session] = lambda: SessionLocal()

    # Add test data
    test_session = SessionLocal()
    test_perspective = Perspective(
        id=1,
        name="Test Perspective",
        description="A test perspective",
        facet_filters="test_filter",
        created_by=1,
        org_id=1
    )
    test_session.add(test_perspective)
    test_session.commit()

    # Run self-test
    from fastapi.testclient import TestClient
    client = TestClient(app)

    response = client.get("/api/perspectives/1/query")
    assert response.status_code == 200
    assert response.json()["name"] == "Test Perspective"

    print("PASS")