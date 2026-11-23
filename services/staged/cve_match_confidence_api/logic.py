"""logic.py -- the service's data/computation layer (one concern, one file).

Queries the real vulnerability-link, server-registry, and advisory models and
computes confidence buckets without performing writes or network calls.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import Base, get_session
from app.models import McpServerRegistry, VulnAdvisory, VulnLink


def _confidence_rows(
    db: Session,
    min_confidence: Optional[float] = None,
    limit: int = 500,
) -> list[Dict[str, Any]]:
    threshold = 0.5 if min_confidence is None else float(min_confidence)

    stmt = (
        select(
            VulnLink.id,
            VulnLink.server_id,
            McpServerRegistry.name,
            VulnLink.advisory_id,
            VulnAdvisory.package,
            VulnLink.match_basis,
            VulnLink.match_value,
            VulnLink.match_confidence,
        )
        .join(McpServerRegistry, McpServerRegistry.server_id == VulnLink.server_id)
        .join(VulnAdvisory, VulnAdvisory.id == VulnLink.advisory_id)
        .where(VulnLink.match_confidence <= threshold)
        .order_by(VulnLink.match_confidence.asc(), VulnLink.id.asc())
        .limit(limit)
    )

    return [
        {
            "link_id": int(link_id),
            "server_id": server_id,
            "server_name": server_name,
            "advisory_id": advisory_id,
            "package": package,
            "match_basis": match_basis,
            "match_value": match_value,
            "match_confidence": float(match_confidence),
        }
        for (
            link_id,
            server_id,
            server_name,
            advisory_id,
            package,
            match_basis,
            match_value,
            match_confidence,
        ) in db.execute(stmt).all()
    ]


def get_match_confidence_summary(
    db: Session,
    min_confidence: Optional[float] = None,
    limit: int = 500,
) -> Dict[str, Any]:
    """Return confidence buckets and the low-confidence linkage records.

    ``min_confidence`` is the inclusive upper bound used to identify links
    considered low confidence. When omitted, the default threshold is 0.5.
    """
    bucket_stmt = (
        select(
            VulnLink.match_basis,
            VulnAdvisory.severity,
            func.min(VulnLink.match_confidence),
            func.max(VulnLink.match_confidence),
            func.avg(VulnLink.match_confidence),
            func.count(VulnLink.id),
        )
        .join(McpServerRegistry, McpServerRegistry.server_id == VulnLink.server_id)
        .join(VulnAdvisory, VulnAdvisory.id == VulnLink.advisory_id)
        .group_by(VulnLink.match_basis, VulnAdvisory.severity)
        .order_by(VulnLink.match_basis.asc(), VulnAdvisory.severity.asc())
    )

    buckets = [
        {
            "match_basis": match_basis,
            "severity": severity or "unknown",
            "min_confidence": float(minimum),
            "max_confidence": float(maximum),
            "avg_confidence": float(average),
            "link_count": int(count),
        }
        for (
            match_basis,
            severity,
            minimum,
            maximum,
            average,
            count,
        ) in db.execute(bucket_stmt).all()
    ]

    total_links = int(
        db.execute(select(func.count()).select_from(VulnLink)).scalar() or 0
    )

    return {
        "buckets": buckets,
        "total_links": total_links,
        "low_confidence_links": _confidence_rows(
            db, min_confidence=min_confidence, limit=limit
        ),
    }


def get_cve_match_confidence(
    db: Session,
    min_confidence: Optional[float] = None,
    limit: int = 500,
) -> Dict[str, Any]:
    """Alias for :func:`get_match_confidence_summary`."""
    return get_match_confidence_summary(
        db, min_confidence=min_confidence, limit=limit
    )


def get_match_confidence(
    db: Session,
    min_confidence: Optional[float] = None,
    limit: int = 500,
) -> Dict[str, Any]:
    """Alias for :func:`get_match_confidence_summary`."""
    return get_match_confidence_summary(
        db, min_confidence=min_confidence, limit=limit
    )


if __name__ == "__main__":
    from fastapi import Depends, FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    testing_session = sessionmaker(bind=engine, expire_on_commit=False)()

    testing_session.add_all(
        [
            McpServerRegistry(
                server_id="srv-confidence-1",
                name="confidence-server-1",
                risk_tier="high",
            ),
            McpServerRegistry(
                server_id="srv-confidence-2",
                name="confidence-server-2",
                risk_tier="medium",
            ),
            VulnAdvisory(
                id=101,
                feed="osv",
                summary="advisory 101",
                severity="HIGH",
                ecosystem="npm",
                package="example-package-101",
                source_url="https://example.invalid/101",
                fetched_at=datetime.now(timezone.utc),
            ),
            VulnAdvisory(
                id=102,
                feed="osv",
                summary="advisory 102",
                severity="MEDIUM",
                ecosystem="npm",
                package="example-package-102",
                source_url="https://example.invalid/102",
                fetched_at=datetime.now(timezone.utc),
            ),
            VulnLink(
                id=201,
                advisory_id=101,
                server_id="srv-confidence-1",
                match_basis="package",
                match_value="example-package-101",
                match_confidence=0.9,
                linked_at=datetime.now(timezone.utc),
            ),
            VulnLink(
                id=202,
                advisory_id=102,
                server_id="srv-confidence-2",
                match_basis="dependency",
                match_value="example-package-102",
                match_confidence=0.4,
                linked_at=datetime.now(timezone.utc),
            ),
            VulnLink(
                id=203,
                advisory_id=101,
                server_id="srv-confidence-2",
                match_basis="manual",
                match_value="example-package-101",
                match_confidence=0.6,
                linked_at=datetime.now(timezone.utc),
            ),
        ]
    )
    testing_session.commit()

    def override_get_session():
        yield testing_session

    local_app = FastAPI()
    local_app.dependency_overrides[get_session] = override_get_session

    @local_app.get("/api/vuln/cve/match-confidence")
    def match_confidence_endpoint(
        min_confidence: Optional[float] = None,
        limit: int = 500,
        db: Session = Depends(get_session),
    ):
        return get_match_confidence_summary(
            db, min_confidence=min_confidence, limit=limit
        )

    response = TestClient(local_app).get(
        "/api/vuln/cve/match-confidence",
        params={"min_confidence": 0.5},
    )
    payload = response.json()
    assert response.status_code == 200
    assert payload["total_links"] == 3
    assert payload["buckets"]
    assert all(
        item["match_confidence"] <= 0.5
        for item in payload["low_confidence_links"]
    )
    print("PASS")