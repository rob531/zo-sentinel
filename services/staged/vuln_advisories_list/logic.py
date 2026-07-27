from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import List
from datetime import datetime
from app.db import get_session
from app.models import VulnAdvisory
import httpx
import json
from sqlalchemy.orm import Session

router = APIRouter(prefix="/api/vuln")

class Advisory(BaseModel):
    id: str
    feed: str
    summary: str
    severity: str
    ecosystem: str
    package: str
    published_at: str
    fetched_at: str

async def list_advisories(limit: int = 100, offset: int = 0) -> List[Advisory]:
    if limit > 500:
        raise HTTPException(status_code=400, detail="Limit cannot exceed 500")

    async with httpx.AsyncClient() as client:
        response = await client.post(
            "http://127.0.0.1:8772/query",
            json={
                "sql": "SELECT id, feed, summary, severity, ecosystem, package, published_at, fetched_at FROM vuln_advisories ORDER BY published_at DESC LIMIT $1 OFFSET $2",
                "params": [limit, offset]
            }
        )
        if response.status_code != 200:
            raise HTTPException(status_code=500, detail="Failed to fetch advisories")

        data = response.json()
        advisories = []
        for row in data:
            advisory = Advisory(
                id=row[0],
                feed=row[1],
                summary=row[2],
                severity=row[3],
                ecosystem=row[4],
                package=row[5],
                published_at=row[6],
                fetched_at=row[7]
            )
            advisories.append(advisory)

        return advisories

if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from app.main import app
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.models import Base

    # Setup in-memory SQLite for testing
    engine = create_engine("sqlite:///:memory:")
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    Base.metadata.create_all(bind=engine)

    # Override the dependency for testing
    app.dependency_overrides[get_session] = lambda: SessionLocal()

    # Populate test data
    session = SessionLocal()
    test_advisories = [
        VulnAdvisory(
            id="1",
            feed="nvd",
            summary="Test advisory 1",
            severity="high",
            ecosystem="python",
            package="test-package-1",
            published_at=datetime.now(),
            fetched_at=datetime.now()
        ),
        VulnAdvisory(
            id="2",
            feed="nvd",
            summary="Test advisory 2",
            severity="medium",
            ecosystem="python",
            package="test-package-2",
            published_at=datetime.now(),
            fetched_at=datetime.now()
        ),
        VulnAdvisory(
            id="3",
            feed="nvd",
            summary="Test advisory 3",
            severity="low",
            ecosystem="python",
            package="test-package-3",
            published_at=datetime.now(),
            fetched_at=datetime.now()
        )
    ]
    session.add_all(test_advisories)
    session.commit()

    # Mock the write_service response
    async def mock_query_endpoint(params):
        limit = params["params"][0]
        offset = params["params"][1]
        session = SessionLocal()
        advisories = session.query(VulnAdvisory).order_by(VulnAdvisory.published_at.desc()).limit(limit).offset(offset).all()
        session.close()
        return [
            [
                adv.id,
                adv.feed,
                adv.summary,
                adv.severity,
                adv.ecosystem,
                adv.package,
                adv.published_at.isoformat(),
                adv.fetched_at.isoformat()
            ]
            for adv in advisories
        ]

    async def mock_post(*args, **kwargs):
        if kwargs["url"] == "http://127.0.0.1:8772/query":
            data = kwargs["json"]
            return httpx.Response(200, json=mock_query_endpoint(data))
        return httpx.Response(500)

    app.dependency_overrides[httpx.AsyncClient] = lambda: httpx.AsyncClient(event_hooks={"request": [mock_post]})

    # Test the endpoint
    client = TestClient(app)
    response = client.get("/api/vuln/advisories?limit=2")
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 2
    assert all(field in data[0] for field in ["id", "feed", "summary", "severity", "ecosystem", "package", "published_at", "fetched_at"])

    print("PASS")