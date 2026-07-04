from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from sqlalchemy import func
from app.db import get_session
from app.models import mcp_server_registry
from typing import Dict

router = APIRouter()

@router.get("/analysis/registry-source-distribution", response_model=Dict[str, int])
def get_registry_source_distribution(session: Session = Depends(get_session)) -> Dict[str, int]:
    source_distribution = (
        session.query(
            mcp_server_registry.source,
            func.count(mcp_server_registry.source).label("count")
        )
        .group_by(mcp_server_registry.source)
        .order_by(func.count(mcp_server_registry.source).desc())
        .limit(5)
        .all()
    )
    return {source: count for source, count in source_distribution}

if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.models import Base
    import pytest

    SQLALCHEMY_DATABASE_URL = "sqlite:///:memory:"
    engine = create_engine(SQLALCHEMY_DATABASE_URL, connect_args={"check_same_thread": False})
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    Base.metadata.create_all(bind=engine)

    def override_get_session():
        try:
            db = TestingSessionLocal()
            yield db
        finally:
            db.close()

    app = APIRouter()
    app.include_router(router)

    client = TestClient(app)

    def test_registry_source_distribution():
        db = TestingSessionLocal()
        db.add(mcp_server_registry(source="source1"))
        db.add(mcp_server_registry(source="source1"))
        db.add(mcp_server_registry(source="source2"))
        db.add(mcp_server_registry(source="source2"))
        db.add(mcp_server_registry(source="source2"))
        db.add(mcp_server_registry(source="source3"))
        db.commit()

        response = client.get("/analysis/registry-source-distribution")
        assert response.status_code == 200
        assert response.json() == {"source2": 3, "source1": 2, "source3": 1}

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("app.db.get_session", override_get_session)
        test_registry_source_distribution()
        print("PASS")