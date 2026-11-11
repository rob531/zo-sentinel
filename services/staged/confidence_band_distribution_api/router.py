from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy import create_engine, insert
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import McpServerRegistry

from .logic import ConfidenceBandResponse, get_confidence_band_distribution


router = APIRouter(prefix="/api", tags=["confidence"])


@router.get(
    "/confidence/band-distribution",
    response_model=ConfidenceBandResponse,
)
def confidence_band_distribution(
    registry_source: Optional[str] = Query(default=None),
    session: Session = Depends(get_session),
):
    return get_confidence_band_distribution(
        session=session,
        registry_source=registry_source,
    )


if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    McpServerRegistry.__table__.create(bind=test_engine)
    TestSession = sessionmaker(bind=test_engine, expire_on_commit=False)
    test_session = TestSession()

    test_session.execute(
        insert(McpServerRegistry),
        [
            {
                "server_id": "server-0",
                "confidence": 0.0,
                "verdict": "test",
                "registry_source": "test",
            },
            {
                "server_id": "server-1",
                "confidence": 0.25,
                "verdict": "test",
                "registry_source": "test",
            },
            {
                "server_id": "server-2",
                "confidence": 0.5,
                "verdict": "test",
                "registry_source": "test",
            },
            {
                "server_id": "server-3",
                "confidence": 0.75,
                "verdict": "test",
                "registry_source": "test",
            },
            {
                "server_id": "server-4",
                "confidence": 1.0,
                "verdict": "test",
                "registry_source": "test",
            },
            {
                "server_id": "server-5",
                "confidence": None,
                "verdict": "test",
                "registry_source": "test",
            },
        ],
    )
    test_session.commit()

    test_app = FastAPI()
    test_app.include_router(router)
    test_app.dependency_overrides[get_session] = lambda: test_session

    response = TestClient(test_app).get("/api/confidence/band-distribution")
    payload = response.json()

    assert response.status_code == 200
    assert payload["total"] == 6
    assert len([band for band in payload["bands"] if band["band"] != "unknown"]) == 5
    assert all(
        band["count"] >= 1
        for band in payload["bands"]
        if band["band"] != "unknown"
    )
    assert next(
        band["count"]
        for band in payload["bands"]
        if band["band"] == "unknown"
    ) == 1

    print("PASS")