from __future__ import annotations

from datetime import datetime, timezone
from statistics import median
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import VulnAdvisory, VulnLink


router = APIRouter(prefix="/vuln/links/orphans", tags=["vulnerability-links"])


class OrphanedLinkResponse(BaseModel):
    id: int
    advisory_id: str
    server_id: str
    match_basis: str
    match_value: str
    match_confidence: float
    linked_at: str


class MatchBasisSummaryResponse(BaseModel):
    match_basis: str
    count: int
    mean_match_confidence: float


class OrphanedLinksResponse(BaseModel):
    total: int
    distinct_servers: int
    by_match_basis: List[MatchBasisSummaryResponse]
    links: List[OrphanedLinkResponse]
    generated_at: str


class OrphanedLinksSummaryResponse(BaseModel):
    total: int
    distinct_servers: int
    by_match_basis: List[MatchBasisSummaryResponse]
    oldest_linked_at: Optional[str]
    newest_linked_at: Optional[str]


_ORPHAN_FROM = """
    FROM vuln_links AS vl
    LEFT JOIN vuln_advisories AS va
        ON vl.advisory_id = va.id
    WHERE va.id IS NULL
"""


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _as_utc(value: Any) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, datetime):
        result = value
    elif isinstance(value, str):
        candidate = value.strip()
        if not candidate:
            return None
        if candidate.endswith("Z"):
            candidate = candidate[:-1] + "+00:00"
        try:
            result = datetime.fromisoformat(candidate)
        except ValueError:
            return None
    else:
        return None

    if result.tzinfo is None:
        return result.replace(tzinfo=timezone.utc)
    return result.astimezone(timezone.utc)


def _iso(value: Any) -> Optional[str]:
    parsed = _as_utc(value)
    if parsed is None:
        return None
    return parsed.isoformat()


def _link_age_stats(session: Session) -> Dict[str, Optional[float]]:
    result = session.execute(
        text(
            """
            SELECT vl.linked_at
            FROM vuln_links AS vl
            LEFT JOIN vuln_advisories AS va
                ON vl.advisory_id = va.id
            WHERE va.id IS NULL
            """
        )
    )
    now = _utc_now()
    ages: List[float] = []

    for row in result:
        linked_at = _as_utc(row[0])
        if linked_at is not None:
            ages.append(max(0.0, (now - linked_at).total_seconds() / 3600.0))

    if not ages:
        return {"min_hours": None, "median_hours": None, "max_hours": None}

    return {
        "min_hours": float(min(ages)),
        "median_hours": float(median(ages)),
        "max_hours": float(max(ages)),
    }


def _get_orphan_counts(session: Session) -> Dict[str, Any]:
    result = session.execute(
        text(
            """
            SELECT
                COUNT(*) AS total,
                COUNT(DISTINCT vl.server_id) AS distinct_servers,
                MIN(vl.linked_at) AS oldest_linked_at,
                MAX(vl.linked_at) AS newest_linked_at
            FROM vuln_links AS vl
            LEFT JOIN vuln_advisories AS va
                ON vl.advisory_id = va.id
            WHERE va.id IS NULL
            """
        )
    ).mappings().one()

    return {
        "total": int(result["total"] or 0),
        "distinct_servers": int(result["distinct_servers"] or 0),
        "oldest_linked_at": result["oldest_linked_at"],
        "newest_linked_at": result["newest_linked_at"],
    }


def _get_match_basis_summary(session: Session) -> List[Dict[str, Any]]:
    result = session.execute(
        text(
            """
            SELECT
                COALESCE(vl.match_basis, 'unknown') AS match_basis,
                COUNT(*) AS count,
                AVG(vl.match_confidence) AS mean_match_confidence
            FROM vuln_links AS vl
            LEFT JOIN vuln_advisories AS va
                ON vl.advisory_id = va.id
            WHERE va.id IS NULL
            GROUP BY COALESCE(vl.match_basis, 'unknown')
            ORDER BY COALESCE(vl.match_basis, 'unknown') ASC
            """
        )
    ).mappings().all()

    return [
        {
            "match_basis": str(row["match_basis"]),
            "count": int(row["count"] or 0),
            "mean_match_confidence": float(row["mean_match_confidence"] or 0.0),
        }
        for row in result
    ]


def get_orphaned_links(
    session: Session,
    limit: int = 200,
    offset: int = 0,
) -> Dict[str, Any]:
    if limit < 1 or limit > 1000:
        raise ValueError("limit must be between 1 and 1000")
    if offset < 0:
        raise ValueError("offset must be non-negative")

    counts = _get_orphan_counts(session)
    by_match_basis = _get_match_basis_summary(session)

    rows = session.execute(
        text(
            """
            SELECT
                vl.id,
                vl.advisory_id,
                vl.server_id,
                vl.match_basis,
                vl.match_value,
                vl.match_confidence,
                vl.linked_at
            FROM vuln_links AS vl
            LEFT JOIN vuln_advisories AS va
                ON vl.advisory_id = va.id
            WHERE va.id IS NULL
            ORDER BY vl.linked_at DESC
            LIMIT :limit OFFSET :offset
            """
        ),
        {"limit": limit, "offset": offset},
    ).mappings().all()

    links = [
        {
            "id": int(row["id"]),
            "advisory_id": str(row["advisory_id"]),
            "server_id": str(row["server_id"]),
            "match_basis": str(row["match_basis"] or "unknown"),
            "match_value": str(row["match_value"] or ""),
            "match_confidence": float(row["match_confidence"] or 0.0),
            "linked_at": _iso(row["linked_at"]) or "",
        }
        for row in rows
    ]

    return {
        "total": counts["total"],
        "distinct_servers": counts["distinct_servers"],
        "by_match_basis": by_match_basis,
        "links": links,
        "generated_at": _utc_now().isoformat(),
    }


def get_orphaned_links_summary(session: Session) -> Dict[str, Any]:
    counts = _get_orphan_counts(session)

    return {
        "total": counts["total"],
        "distinct_servers": counts["distinct_servers"],
        "by_match_basis": _get_match_basis_summary(session),
        "oldest_linked_at": _iso(counts["oldest_linked_at"]),
        "newest_linked_at": _iso(counts["newest_linked_at"]),
    }


def get_orphaned_link_age_stats(session: Session) -> Dict[str, Optional[float]]:
    return _link_age_stats(session)


get_orphan_links = get_orphaned_links
get_orphan_links_summary = get_orphaned_links_summary
get_orphaned_advisory_links = get_orphaned_links
get_orphaned_advisory_links_summary = get_orphaned_links_summary


@router.get("", response_model=OrphanedLinksResponse)
def read_orphaned_links(
    limit: int = Query(200, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    session: Session = Depends(get_session),
) -> Dict[str, Any]:
    return get_orphaned_links(session=session, limit=limit, offset=offset)


@router.get("/summary", response_model=OrphanedLinksSummaryResponse)
def read_orphaned_links_summary(
    session: Session = Depends(get_session),
) -> Dict[str, Any]:
    return get_orphaned_links_summary(session=session)


def _self_test() -> None:
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
    TestingSession = sessionmaker(bind=engine)

    with engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE vuln_advisories (
                    id VARCHAR PRIMARY KEY,
                    feed VARCHAR,
                    summary VARCHAR,
                    severity VARCHAR,
                    ecosystem VARCHAR,
                    package VARCHAR,
                    source_url VARCHAR,
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
                    advisory_id VARCHAR,
                    server_id VARCHAR,
                    match_basis VARCHAR,
                    match_value VARCHAR,
                    match_confidence REAL,
                    linked_at DATETIME
                )
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO vuln_advisories
                    (id, feed, summary, severity, ecosystem, package, source_url, published_at)
                VALUES
                    ('existing', 'test', 'summary', 'high', 'pypi', 'package', 'url', CURRENT_TIMESTAMP)
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO vuln_links
                    (id, advisory_id, server_id, match_basis, match_value, match_confidence, linked_at)
                VALUES
                    (1, 'existing', 'server-1', 'package', 'package-a', 0.9, CURRENT_TIMESTAMP),
                    (2, 'missing-1', 'server-2', 'cve', 'CVE-1', 0.8, CURRENT_TIMESTAMP),
                    (3, 'missing-2', 'server-3', 'cve', 'CVE-2', 0.7, CURRENT_TIMESTAMP)
                """
            )
        )

    local_app = FastAPI()
    local_app.include_router(router, prefix="/api")

    def override_get_session():
        db = TestingSession()
        try:
            yield db
        finally:
            db.close()

    local_app.dependency_overrides[get_session] = override_get_session

    try:
        with TestClient(local_app) as client:
            response = client.get("/api/vuln/links/orphans")
            assert response.status_code == 200
            payload = response.json()
            assert payload["total"] == 2
            assert payload["distinct_servers"] == 2
            assert any(
                item["match_basis"] == "cve" and item["count"] == 2
                for item in payload["by_match_basis"]
            )
    finally:
        engine.dispose()

    print("PASS")


if __name__ == "__main__":
    _self_test()