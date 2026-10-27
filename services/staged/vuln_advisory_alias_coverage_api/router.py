from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import VulnAdvisory

if __package__:
    from .logic import compute_alias_coverage
else:
    from logic import compute_alias_coverage


router = APIRouter(prefix="/api")


@router.get("/vuln/advisories/alias-coverage")
def get_alias_coverage(session: Session = Depends(get_session)) -> dict[str, Any]:
    rows = session.execute(
        select(VulnAdvisory.feed, VulnAdvisory.severity, VulnAdvisory.aliases).order_by(
            VulnAdvisory.feed,
            VulnAdvisory.severity,
        )
    ).all()

    advisories = [
        {
            "feed": feed,
            "severity": severity,
            "aliases": aliases,
        }
        for feed, severity, aliases in rows
    ]
    return compute_alias_coverage(advisories)


if __name__ == "__main__":
    import json

    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestSession = sessionmaker(bind=test_engine, expire_on_commit=False)

    with test_engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE vuln_advisories (
                    id INTEGER PRIMARY KEY,
                    feed TEXT,
                    severity TEXT,
                    aliases JSON
                )
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO vuln_advisories (id, feed, severity, aliases)
                VALUES (:id, :feed, :severity, :aliases)
                """
            ),
            [
                {
                    "id": 1,
                    "feed": "osv",
                    "severity": "HIGH",
                    "aliases": json.dumps(["CVE-2024-0001"]),
                },
                {
                    "id": 2,
                    "feed": "osv",
                    "severity": "HIGH",
                    "aliases": json.dumps([]),
                },
                {
                    "id": 3,
                    "feed": "npm",
                    "severity": "MEDIUM",
                    "aliases": None,
                },
                {
                    "id": 4,
                    "feed": "osv",
                    "severity": "LOW",
                    "aliases": json.dumps(["GHSA-test-0001"]),
                },
            ],
        )

    test_app = FastAPI()
    test_app.include_router(router)

    def override_get_session():
        with TestSession() as session:
            yield session

    test_app.dependency_overrides[get_session] = override_get_session

    response = TestClient(test_app).get("/api/vuln/advisories/alias-coverage")
    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] == 4
    assert payload["with_aliases"] == 2
    assert payload["coverage_ratio"] == 0.5
    print("PASS")