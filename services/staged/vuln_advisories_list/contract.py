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

async def query_write_service(sql: str, params: tuple) -> List[dict]:
    async with httpx.AsyncClient() as client:
        response = await client.post(
            "http://127.0.0.1:8772/query",
            json={"sql": sql, "params": params}
        )
        if response.status_code != 200:
            raise HTTPException(status_code=response.status_code, detail="Query failed")
        return response.json()

async def list_advisories(limit: int = 100, offset: int = 0) -> List[Advisory]:
    if limit > 500:
        raise HTTPException(status_code=400, detail="Limit cannot exceed 500")

    sql = """
    SELECT id, feed, summary, severity, ecosystem, package, published_at, fetched_at
    FROM vuln_advisories
    ORDER BY published_at DESC
    LIMIT $1 OFFSET $2
    """
    params = (limit, offset)

    rows = await query_write_service(sql, params)

    advisories = []
    for row in rows:
        advisory = Advisory(
            id=row["id"],
            feed=row["feed"],
            summary=row["summary"],
            severity=row["severity"],
            ecosystem=row["ecosystem"],
            package=row["package"],
            published_at=row["published_at"],
            fetched_at=row["fetched_at"]
        )
        advisories.append(advisory)

    return advisories

router.get("/advisories", response_model=List[Advisory])(list_advisories)

if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from fastapi import FastAPI
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.models import Base

    app = FastAPI()
    app.include_router(router)

    # Setup in-memory SQLite database for testing
    engine = create_engine("sqlite:///:memory:")
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    Base.metadata.create_all(bind=engine)

    # Override the get_session dependency for testing
    app.dependency_overrides[get_session] = lambda: SessionLocal()

    # Mock the write_service query endpoint
    async def mock_query_write_service(sql: str, params: tuple) -> List[dict]:
        if "vuln_advisories" in sql:
            return [
                {
                    "id": "1",
                    "feed": "nvd",
                    "summary": "Critical vulnerability in package A",
                    "severity": "critical",
                    "ecosystem": "npm",
                    "package": "package-a",
                    "published_at": "2023-01-01T00:00:00Z",
                    "fetched_at": "2023-01-02T00:00:00Z"
                },
                {
                    "id": "2",
                    "feed": "nvd",
                    "summary": "High vulnerability in package B",
                    "severity": "high",
                    "ecosystem": "pypi",
                    "package": "package-b",
                    "published_at": "2023-01-02T00:00:00Z",
                    "fetched_at": "2023-01-03T00:00:00Z"
                },
                {
                    "id": "3",
                    "feed": "nvd",
                    "summary": "Medium vulnerability in package C",
                    "severity": "medium",
                    "ecosystem": "rubygems",
                    "package": "package-c",
                    "published_at": "2023-01-03T00:00:00Z",
                    "fetched_at": "2023-01-04T00:00:00Z"
                }
            ]
        return []

    app.dependency_overrides[query_write_service] = mock_query_write_service

    client = TestClient(app)

    response = client.get("/api/vuln/advisories?limit=2")
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 2
    assert all(field in data[0] for field in ["id", "feed", "summary", "severity", "ecosystem", "package", "published_at", "fetched_at"])

    print("PASS")