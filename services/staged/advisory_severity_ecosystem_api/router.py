from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import VulnAdvisory, VulnLink

try:
    from .logic import get_severity_ecosystem_mix as _provided_logic
except ImportError:
    _provided_logic = None

router = APIRouter(prefix="/api")


def _advisory_filters(feed: Optional[str], days: Optional[int]) -> Dict[str, Any]:
    filters: List[str] = []
    params: Dict[str, Any] = {}

    if feed:
        filters.append("feed = :feed")
        params["feed"] = feed

    if days is not None:
        filters.append("published_at >= :cutoff")
        params["cutoff"] = datetime.now(timezone.utc) - timedelta(days=days)

    return {"where": " AND ".join(filters) if filters else "1 = 1", "params": params}


def _query_mix(session: Session, feed: Optional[str], days: Optional[int]) -> Dict[str, Any]:
    clause = _advisory_filters(feed, days)
    where = clause["where"]
    params = clause["params"]

    severity_rows = session.execute(
        text(
            f"""
            SELECT COALESCE(severity, 'unknown') AS severity, COUNT(*) AS count
            FROM vuln_advisories
            WHERE {where}
            GROUP BY COALESCE(severity, 'unknown')
            ORDER BY count DESC, severity ASC
            """
        ),
        params,
    ).all()

    ecosystem_rows = session.execute(
        text(
            f"""
            SELECT COALESCE(ecosystem, 'unknown') AS ecosystem, COUNT(*) AS count
            FROM vuln_advisories
            WHERE {where}
            GROUP BY COALESCE(ecosystem, 'unknown')
            ORDER BY count DESC, ecosystem ASC
            """
        ),
        params,
    ).all()

    feed_rows = session.execute(
        text(
            f"""
            SELECT COALESCE(feed, 'unknown') AS feed, COUNT(*) AS count
            FROM vuln_advisories
            WHERE {where}
            GROUP BY COALESCE(feed, 'unknown')
            ORDER BY count DESC, feed ASC
            """
        ),
        params,
    ).all()

    affected_server_rows = session.execute(
        text(
            f"""
            SELECT
                COALESCE(va.ecosystem, 'unknown') AS ecosystem,
                COUNT(DISTINCT vl.server_id) AS affected_servers
            FROM vuln_advisories AS va
            LEFT JOIN vuln_links AS vl ON vl.advisory_id = va.id
            WHERE {where.replace("feed", "va.feed").replace("published_at", "va.published_at")}
            GROUP BY COALESCE(va.ecosystem, 'unknown')
            ORDER BY ecosystem ASC
            """
        ),
        params,
    ).all()

    affected_servers = {
        str(row[0]): int(row[1] or 0) for row in affected_server_rows
    }

    by_severity = [
        {"severity": str(row[0]), "count": int(row[1])}
        for row in severity_rows
    ]
    by_ecosystem = [
        {
            "ecosystem": str(row[0]),
            "count": int(row[1]),
            "affected_servers": affected_servers.get(str(row[0]), 0),
        }
        for row in ecosystem_rows
    ]
    by_feed = [{"feed": str(row[0]), "count": int(row[1])} for row in feed_rows]

    return {
        "total_advisories": sum(item["count"] for item in by_severity),
        "by_severity": by_severity,
        "by_ecosystem": by_ecosystem,
        "by_feed": by_feed,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


@router.get("/advisories/severity-ecosystem-mix")
def severity_ecosystem_mix(
    feed: Optional[str] = Query(default=None),
    days: Optional[int] = Query(default=30),
    session: Session = Depends(get_session),
) -> Dict[str, Any]:
    return _query_mix(session, feed, days)


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
    VulnAdvisory.__table__.create(engine, checkfirst=True)
    VulnLink.__table__.create(engine, checkfirst=True)

    now = datetime.now(timezone.utc)
    with engine.begin() as connection:
        connection.execute(
            VulnAdvisory.__table__.insert(),
            [
                {
                    "id": 1,
                    "feed": "osv",
                    "severity": "high",
                    "ecosystem": "npm",
                    "package": "example-a",
                    "published_at": now,
                    "fetched_at": now,
                    "content_hash": "hash-a",
                    "affected_ranges": None,
                    "aliases": None,
                    "identities": None,
                    "source_url": "",
                    "summary": "",
                },
                {
                    "id": 2,
                    "feed": "nvd",
                    "severity": "critical",
                    "ecosystem": "pypi",
                    "package": "example-b",
                    "published_at": now,
                    "fetched_at": now,
                    "content_hash": "hash-b",
                    "affected_ranges": None,
                    "aliases": None,
                    "identities": None,
                    "source_url": "",
                    "summary": "",
                },
            ],
        )
        connection.execute(
            VulnLink.__table__.insert(),
            [
                {
                    "id": 1,
                    "advisory_id": 1,
                    "server_id": 10,
                    "match_basis": "package",
                    "match_confidence": 0.95,
                    "match_value": "example-a",
                    "linked_at": now,
                },
                {
                    "id": 2,
                    "advisory_id": 2,
                    "server_id": 20,
                    "match_basis": "package",
                    "match_confidence": 0.90,
                    "match_value": "example-b",
                    "linked_at": now,
                },
            ],
        )

    SessionLocal = sessionmaker(bind=engine)

    def override_get_session():
        db = SessionLocal()
        try:
            yield db
        finally:
            db.close()

    test_app = FastAPI()
    test_app.include_router(router)
    test_app.dependency_overrides[get_session] = override_get_session

    with TestClient(test_app) as client:
        response = client.get(
            "/api/advisories/severity-ecosystem-mix",
            params={"days": 30},
        )
        assert response.status_code == 200
        payload = response.json()
        assert set(payload) == {
            "total_advisories",
            "by_severity",
            "by_ecosystem",
            "by_feed",
            "generated_at",
        }
        assert any(
            item["severity"] == "high" and item["count"] == 1
            for item in payload["by_severity"]
        )
        assert any(
            item["ecosystem"] == "npm"
            and item["count"] == 1
            and item["affected_servers"] == 1
            for item in payload["by_ecosystem"]
        )
        assert any(
            item["feed"] == "osv" and item["count"] == 1
            for item in payload["by_feed"]
        )

    print("PASS")