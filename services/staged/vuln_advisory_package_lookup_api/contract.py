from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Dict, List, Optional

from app.db import get_session
from app.models import VulnAdvisory
from fastapi import APIRouter, Depends, FastAPI, Query
from fastapi.testclient import TestClient
from pydantic import BaseModel
from sqlalchemy import create_engine, or_
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

router = APIRouter(prefix="/api")


class AdvisoryResponse(BaseModel):
    id: int
    feed: str
    summary: str
    severity: str
    ecosystem: str
    package: str
    source_url: Optional[str]
    published_at: Optional[datetime]
    fetched_at: Optional[datetime]
    aliases: List[str]


class AdvisoryLookupResponse(BaseModel):
    count: int
    advisories: List[AdvisoryResponse]


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _normalize_aliases(value: Any) -> List[str]:
    if value is None:
        return []
    if isinstance(value, str):
        try:
            decoded = json.loads(value)
        except (TypeError, ValueError):
            return []
        return decoded if isinstance(decoded, list) else []
    if isinstance(value, (list, tuple)):
        return [str(item) for item in value]
    return []


@router.get("/vuln/advisories", response_model=AdvisoryLookupResponse)
def lookup_advisories(
    package: Optional[str] = Query(default=None),
    ecosystem: Optional[str] = Query(default=None),
    severity: Optional[str] = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    session: Session = Depends(get_session),
) -> Dict[str, Any]:
    statement = session.query(VulnAdvisory)

    if package:
        escaped_package = _escape_like(package)
        statement = statement.filter(
            or_(
                VulnAdvisory.package.ilike(escaped_package, escape="\\"),
                VulnAdvisory.package.ilike(escaped_package + "%", escape="\\"),
            )
        )

    if ecosystem:
        statement = statement.filter(
            VulnAdvisory.ecosystem.ilike(_escape_like(ecosystem), escape="\\")
        )

    if severity:
        statement = statement.filter(
            VulnAdvisory.severity.ilike(_escape_like(severity), escape="\\")
        )

    rows = (
        statement.order_by(VulnAdvisory.published_at.desc().nullslast(), VulnAdvisory.id)
        .limit(limit)
        .all()
    )

    advisories = [
        {
            "id": row.id,
            "feed": row.feed,
            "summary": row.summary,
            "severity": row.severity,
            "ecosystem": row.ecosystem,
            "package": row.package,
            "source_url": row.source_url,
            "published_at": row.published_at,
            "fetched_at": row.fetched_at,
            "aliases": _normalize_aliases(row.aliases),
        }
        for row in rows
    ]

    return {"count": len(advisories), "advisories": advisories}


def _run_self_test() -> None:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestingSessionLocal = sessionmaker(
        autocommit=False,
        autoflush=False,
        bind=engine,
    )

    VulnAdvisory.__table__.create(engine, checkfirst=True)

    with TestingSessionLocal() as session:
        session.add_all(
            [
                VulnAdvisory(
                    id=1,
                    feed="osv",
                    summary="Prototype pollution advisory",
                    severity="HIGH",
                    ecosystem="npm",
                    package="lodash",
                    affected_ranges=[{"introduced": "0"}],
                    aliases=["CVE-2024-0001", "GHSA-test-0001"],
                    source_url="https://example.test/advisories/1",
                    published_at=datetime(2024, 1, 3, 12, 0, 0),
                    fetched_at=datetime(2024, 1, 4, 12, 0, 0),
                    identities=["osv:GHSA-test-0001"],
                    content_hash="hash-1",
                ),
                VulnAdvisory(
                    id=2,
                    feed="osv",
                    summary="Express advisory",
                    severity="MEDIUM",
                    ecosystem="npm",
                    package="express",
                    affected_ranges=[{"introduced": "0"}],
                    aliases=["CVE-2024-0002"],
                    source_url="https://example.test/advisories/2",
                    published_at=datetime(2024, 1, 2, 12, 0, 0),
                    fetched_at=datetime(2024, 1, 3, 12, 0, 0),
                    identities=["osv:CVE-2024-0002"],
                    content_hash="hash-2",
                ),
                VulnAdvisory(
                    id=3,
                    feed="osv",
                    summary="Requests advisory",
                    severity="LOW",
                    ecosystem="pypi",
                    package="requests",
                    affected_ranges=[{"introduced": "0"}],
                    aliases=[],
                    source_url="https://example.test/advisories/3",
                    published_at=datetime(2024, 1, 1, 12, 0, 0),
                    fetched_at=datetime(2024, 1, 2, 12, 0, 0),
                    identities=[],
                    content_hash="hash-3",
                ),
            ]
        )
        session.commit()

    def override_get_session():
        db = TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()

    test_app = FastAPI()
    test_app.include_router(router)
    test_app.dependency_overrides[get_session] = override_get_session

    with TestClient(test_app) as client:
        response = client.get(
            "/api/vuln/advisories",
            params={"package": "lodash", "ecosystem": "npm", "severity": "HIGH"},
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["count"] == 1
        assert len(payload["advisories"]) == 1
        assert payload["advisories"][0]["package"] == "lodash"
        assert isinstance(payload["advisories"][0]["aliases"], list)

        response = client.get(
            "/api/vuln/advisories",
            params={"ecosystem": "npm"},
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["count"] == 2
        assert len(payload["advisories"]) == 2

        response = client.get(
            "/api/vuln/advisories",
            params={"package": "package-that-does-not-exist"},
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["count"] == 0
        assert payload["advisories"] == []

    engine.dispose()
    print("PASS")


if __name__ == "__main__":
    _run_self_test()