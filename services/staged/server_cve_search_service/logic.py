from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

project_root = str(Path(__file__).resolve().parents[3])
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from app.db import get_session
from app.models import VulnAdvisory, VulnLink


def get_server_cve_search(
    server_id: int | str,
    db: Session = Depends(get_session),
) -> dict[str, Any]:
    rows = db.execute(
        select(
            VulnAdvisory.id,
            VulnAdvisory.feed,
            VulnAdvisory.severity,
            VulnAdvisory.summary,
            VulnAdvisory.source_url,
            VulnAdvisory.published_at,
        )
        .join(VulnLink, VulnAdvisory.id == VulnLink.advisory_id)
        .where(VulnLink.server_id == str(server_id))
        .distinct()
        .order_by(VulnAdvisory.id)
    ).all()
    return {
        "server_id": server_id,
        "advisories": [
            {
                "id": row.id,
                "feed": row.feed,
                "severity": row.severity,
                "summary": row.summary,
                "source_url": row.source_url,
                "published_at": row.published_at,
            }
            for row in rows
        ],
    }


def _self_test() -> None:
    from fastapi.testclient import TestClient
    from sqlalchemy.pool import StaticPool

    from app.db import Base

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    testing_session = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    with testing_session() as db:
        advisories = [
            VulnAdvisory(
                id="CVE-2025-0001",
                feed="nvd",
                severity="HIGH",
                summary="First test advisory",
                source_url="https://example.com/CVE-2025-0001",
                published_at=datetime(2025, 1, 1),
            ),
            VulnAdvisory(
                id="CVE-2025-0002",
                feed="osv",
                severity="MEDIUM",
                summary="Second test advisory",
                source_url="https://example.com/CVE-2025-0002",
                published_at=datetime(2025, 1, 2),
            ),
        ]
        db.add_all(advisories)
        db.flush()
        db.add_all(
            [
                VulnLink(
                    id=1,
                    advisory_id=advisories[0].id,
                    server_id="1",
                    match_basis="package_exact",
                    match_value="pkg-a",
                    match_confidence=1.0,
                ),
                VulnLink(
                    id=2,
                    advisory_id=advisories[1].id,
                    server_id="1",
                    match_basis="package_exact",
                    match_value="pkg-b",
                    match_confidence=1.0,
                ),
                VulnLink(
                    id=3,
                    advisory_id=advisories[0].id,
                    server_id="2",
                    match_basis="package_exact",
                    match_value="pkg-a",
                    match_confidence=1.0,
                ),
            ]
        )
        db.commit()

    def override_get_session():
        with testing_session() as db:
            yield db

    test_app = FastAPI()

    @test_app.get("/api/servers/{server_id}/cve-search")
    def cve_search_endpoint(
        server_id: int,
        db: Session = Depends(get_session),
    ) -> dict[str, Any]:
        return get_server_cve_search(server_id, db)

    test_app.dependency_overrides[get_session] = override_get_session
    with TestClient(test_app) as client:
        response = client.get("/api/servers/1/cve-search")

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["server_id"] == 1, payload
    assert len(payload["advisories"]) == 2, payload
    assert payload["advisories"][0]["severity"] in {
        "CRITICAL",
        "HIGH",
        "MEDIUM",
        "LOW",
        "UNKNOWN",
    }, payload


if __name__ == "__main__":
    try:
        _self_test()
    except Exception as exc:
        print("FAIL: %r" % (exc,))
        sys.exit(1)
    print("PASS")
    sys.exit(0)