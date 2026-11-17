from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from fastapi import Depends, FastAPI, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import VulnAdvisory, VulnLink


def _json_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, str):
        try:
            decoded = json.loads(value)
        except (TypeError, ValueError):
            return []
        return decoded if isinstance(decoded, list) else []
    if isinstance(value, list):
        return value
    return []


def get_vuln_advisory_aliases(
    advisory_id: int,
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    advisory = session.execute(
        select(
            VulnAdvisory.id,
            VulnAdvisory.feed,
            VulnAdvisory.summary,
            VulnAdvisory.severity,
            VulnAdvisory.ecosystem,
            VulnAdvisory.package,
            VulnAdvisory.affected_ranges,
            VulnAdvisory.aliases,
            VulnAdvisory.identities,
            VulnAdvisory.source_url,
            VulnAdvisory.published_at,
        ).where(VulnAdvisory.id == advisory_id)
    ).mappings().one_or_none()

    if advisory is None:
        raise HTTPException(status_code=404, detail="Advisory not found")

    links = session.execute(
        select(
            VulnLink.server_id,
            VulnLink.match_basis,
            VulnLink.match_value,
            VulnLink.match_confidence,
        )
        .where(VulnLink.advisory_id == advisory_id)
        .order_by(VulnLink.id)
    ).mappings().all()

    matched_servers = [
        {
            "server_id": link["server_id"],
            "match_basis": link["match_basis"],
            "match_value": link["match_value"],
            "match_confidence": link["match_confidence"],
        }
        for link in links
    ]

    return {
        "advisory_id": advisory["id"],
        "feed": advisory["feed"],
        "severity": advisory["severity"],
        "ecosystem": advisory["ecosystem"],
        "package": advisory["package"],
        "summary": advisory["summary"],
        "source_url": advisory["source_url"],
        "published_at": advisory["published_at"],
        "aliases": [str(item) for item in _json_list(advisory["aliases"])],
        "identities": [str(item) for item in _json_list(advisory["identities"])],
        "affected_ranges": _json_list(advisory["affected_ranges"]),
        "matched_servers": matched_servers,
        "matched_server_count": len(matched_servers),
    }


def _run_self_test() -> None:
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    VulnAdvisory.__table__.create(engine, checkfirst=True)
    VulnLink.__table__.create(engine, checkfirst=True)

    testing_session = sessionmaker(bind=engine, expire_on_commit=False)

    def override_get_session():
        with testing_session() as session:
            yield session

    app = FastAPI()
    app.dependency_overrides[get_session] = override_get_session

    @app.get("/api/vuln/advisories/{advisory_id}/aliases")
    def read_advisory_aliases(advisory_id: int, session: Session = Depends(get_session)):
        return get_vuln_advisory_aliases(advisory_id, session)

    with testing_session() as session:
        session.add(
            VulnAdvisory(
                id=1,
                feed="osv",
                summary="Example advisory",
                severity="high",
                ecosystem="pypi",
                package="example-package",
                affected_ranges=[{"introduced": "1.0.0", "fixed": "1.2.0"}],
                aliases=["CVE-2026-0001", "GHSA-example"],
                identities=["example-package"],
                source_url="https://example.test/advisory/1",
                published_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
            )
        )
        session.add_all(
            [
                VulnLink(
                    advisory_id=1,
                    server_id=101,
                    match_basis="package",
                    match_value="example-package",
                    match_confidence=0.99,
                ),
                VulnLink(
                    advisory_id=1,
                    server_id=102,
                    match_basis="alias",
                    match_value="CVE-2026-0001",
                    match_confidence=0.95,
                ),
            ]
        )
        session.commit()

    with TestClient(app) as client:
        response = client.get("/api/vuln/advisories/1/aliases")
        assert response.status_code == 200
        payload = response.json()
        assert len(payload["aliases"]) == 2
        assert payload["matched_server_count"] == 2

        missing = client.get("/api/vuln/advisories/999/aliases")
        assert missing.status_code == 404
        assert "detail" in missing.json()

    print("PASS")


if __name__ == "__main__":
    _run_self_test()