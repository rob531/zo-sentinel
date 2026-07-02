from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from sqlalchemy import func
from app.db import get_session
from app.models import mcp_server_registry
from pydantic import BaseModel
from typing import Dict

router = APIRouter()

class SourceDistribution(BaseModel):
    count: int
    percentage: float

class ServerRegistrySourceDistribution(BaseModel):
    registry_source: Dict[str, SourceDistribution]

@router.get("/server-registry-source-distribution", response_model=ServerRegistrySourceDistribution)
def get_server_registry_source_distribution(session: Session = Depends(get_session)):
    total_count = session.query(func.count(mcp_server_registry.id)).scalar()
    source_distribution = session.query(
        mcp_server_registry.registry_source,
        func.count(mcp_server_registry.id).label('count')
    ).group_by(mcp_server_registry.registry_source).all()

    result = {}
    for source, count in source_distribution:
        percentage = (count / total_count) * 100
        result[source] = SourceDistribution(count=count, percentage=percentage)

    return {"registry_source": result}

if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.models import Base

    SQLALCHEMY_DATABASE_URL = "sqlite:///:memory:"
    engine = create_engine(SQLALCHEMY_DATABASE_URL)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    Base.metadata.create_all(bind=engine)

    def override_get_session():
        try:
            db = SessionLocal()
            yield db
        finally:
            db.close()

    app = APIRouter()
    app.include_router(router)

    from fastapi import FastAPI
    test_app = FastAPI()
    test_app.include_router(app)

    test_app.dependency_overrides[get_session] = override_get_session

    client = TestClient(test_app)

    test_data = [
        {"registry_source": "source1"},
        {"registry_source": "source2"},
        {"registry_source": "source2"},
        {"registry_source": "source3"},
        {"registry_source": "source3"},
        {"registry_source": "source3"},
    ]

    db = SessionLocal()
    for data in test_data:
        db.add(mcp_server_registry(**data))
    db.commit()
    db.close()

    response = client.get("/server-registry-source-distribution")
    assert response.status_code == 200
    assert response.json() == {
        "registry_source": {
            "source1": {"count": 1, "percentage": 20.0},
            "source2": {"count": 2, "percentage": 40.0},
            "source3": {"count": 3, "percentage": 60.0},
        }
    }

    print("PASS")