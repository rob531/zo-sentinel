from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import VulnAdvisory, VulnLink  # noqa: F401


router = APIRouter(prefix="/api/vuln/family-severity", tags=["vulnerability"])


class FamilySeverity(BaseModel):
    ecosystem: Optional[str] = None
    package: Optional[str] = None
    severity: Optional[str] = None
    advisory_count: int = 0
    linked_server_count: int = 0
    avg_match_confidence: float = 0.0
    latest_published_at: Optional[datetime] = None
    sample_advisory_ids: list[int] = Field(default_factory=list)


class FamilySeverityResponse(BaseModel):
    days: int
    generated_at: datetime
    families: list[FamilySeverity]


def _as_datetime(value: object) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    return None


@router.get("", response_model=FamilySeverityResponse)
def get_family_severity(
    days: int = Query(90),
    severity: Optional[str] = Query(None),
    ecosystem: Optional[str] = Query(None),
    limit: int = Query(50),
    session: Session = Depends(get_session),
) -> FamilySeverityResponse:
    days = max(1, min(365, days))
    limit = max(1, min(200, limit))
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    filters = ["a.published_at >= :cutoff"]
    params: dict[str, object] = {"cutoff": cutoff}

    if severity is not None:
        filters.append("a.severity = :severity")
        params["severity"] = severity
    if ecosystem is not None:
        filters.append("a.ecosystem = :ecosystem")
        params["ecosystem"] = ecosystem

    where_sql = " AND ".join(filters)
    params["limit"] = limit

    grouped_sql = f"""
        SELECT
            a.ecosystem AS ecosystem,
            a.package AS package,
            a.severity AS severity,
            COUNT(DISTINCT a.id) AS advisory_count,
            COUNT(DISTINCT l.server_id) AS linked_server_count,
            COALESCE(AVG(l.match_confidence), 0.0) AS avg_match_confidence,
            MAX(a.published_at) AS latest_published_at
        FROM vuln_advisories AS a
        INNER JOIN vuln_links AS l ON l.advisory_id = a.id
        WHERE {where_sql}
        GROUP BY a.ecosystem, a.package, a.severity
        ORDER BY linked_server_count DESC, a.severity ASC
        LIMIT :limit
    """

    grouped_rows = session.execute(text(grouped_sql), params).mappings().all()

    sample_sql = f"""
        SELECT DISTINCT
            a.ecosystem AS ecosystem,
            a.package AS package,
            a.severity AS severity,
            a.id AS advisory_id
        FROM vuln_advisories AS a
        INNER JOIN vuln_links AS l ON l.advisory_id = a.id
        WHERE {where_sql}
        ORDER BY a.published_at DESC
    """
    sample_rows = session.execute(text(sample_sql), params).mappings().all()

    sample_ids: dict[tuple[object, object, object], list[int]] = {}
    for row in sample_rows:
        key = (row["ecosystem"], row["package"], row["severity"])
        sample_ids.setdefault(key, []).append(int(row["advisory_id"]))

    families: list[FamilySeverity] = []
    for row in grouped_rows:
        key = (row["ecosystem"], row["package"], row["severity"])
        families.append(
            FamilySeverity(
                ecosystem=row["ecosystem"],
                package=row["package"],
                severity=row["severity"],
                advisory_count=int(row["advisory_count"] or 0),
                linked_server_count=int(row["linked_server_count"] or 0),
                avg_match_confidence=float(row["avg_match_confidence"] or 0.0),
                latest_published_at=_as_datetime(row["latest_published_at"]),
                sample_advisory_ids=sample_ids.get(key, []),
            )
        )

    return FamilySeverityResponse(
        days=days,
        generated_at=datetime.now(timezone.utc),
        families=families,
    )


if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE vuln_advisories (
                    id INTEGER PRIMARY KEY,
                    ecosystem TEXT,
                    package TEXT,
                    severity TEXT,
                    published_at DATETIME
                )
                """
            )
        )
        connection.execute(
            text(
                """
                CREATE TABLE vuln_links (
                    id INTEGER PRIMARY KEY,
                    advisory_id INTEGER,
                    server_id INTEGER,
                    match_confidence REAL
                )
                """
            )
        )
        now = datetime.now(timezone.utc)
        connection.execute(
            text(
                """
                INSERT INTO vuln_advisories
                    (id, ecosystem, package, severity, published_at)
                VALUES
                    (1, 'npm', 'shared-package', 'high', :now),
                    (2, 'npm', 'shared-package', 'high', :now),
                    (3, 'pypi', 'other-package', 'medium', :now)
                """
            ),
            {"now": now},
        )
        connection.execute(
            text(
                """
                INSERT INTO vuln_links
                    (id, advisory_id, server_id, match_confidence)
                VALUES
                    (1, 1, 10, 0.4),
                    (2, 1, 20, 0.6),
                    (3, 2, 20, 0.8),
                    (4, 3, 30, 0.7)
                """
            )
        )

    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    that_app = FastAPI()
    that_app.include_router(router)

    def override_get_session():
        with SessionLocal() as test_session:
            yield test_session

    that_app.dependency_overrides[get_session] = override_get_session

    with TestClient(that_app) as client:
        response = client.get("/api/vuln/family-severity")
        assert response.status_code == 200
        payload = response.json()
        assert len(payload["families"]) == 2
        shared = next(
            family
            for family in payload["families"]
            if family["package"] == "shared-package"
        )
        assert shared["advisory_count"] == 2
        assert shared["linked_server_count"] == 2
        assert 0.0 <= shared["avg_match_confidence"] <= 1.0

    print("PASS")