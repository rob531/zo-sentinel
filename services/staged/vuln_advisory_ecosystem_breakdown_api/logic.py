from typing import Any, Dict, List, Optional

from fastapi import Depends, FastAPI, HTTPException, Query
from sqlalchemy import text
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import get_session
from app.models import VulnAdvisory


_SEVERITIES = ("critical", "high", "medium", "low", "unknown")


def _published_at_string(value: Any) -> Optional[str]:
    if value is None:
        return None
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


def get_ecosystem_breakdown(
    ecosystem: Optional[str] = None,
    limit: int = 20,
    db: Session = Depends(get_session),
) -> Dict[str, Any]:
    if limit < 1 or limit > 100:
        raise HTTPException(
            status_code=422,
            detail="limit must be between 1 and 100",
        )

    result = db.execute(
        text(
            """
            SELECT
                COALESCE(ecosystem, 'unknown') AS ecosystem,
                COALESCE(severity, 'unknown') AS severity,
                COUNT(*) AS advisory_count,
                MAX(published_at) AS latest_published_at
            FROM vuln_advisories
            WHERE (
                :ecosystem IS NULL
                OR COALESCE(ecosystem, 'unknown') = :ecosystem
            )
            GROUP BY
                COALESCE(ecosystem, 'unknown'),
                COALESCE(severity, 'unknown')
            """
        ),
        {"ecosystem": ecosystem},
    )

    grouped: Dict[str, Dict[str, Any]] = {}
    total = 0

    for row in result.mappings():
        ecosystem_name = str(row["ecosystem"])
        raw_severity = str(row["severity"]).strip().lower()
        severity_name = raw_severity if raw_severity in _SEVERITIES else "unknown"
        count = int(row["advisory_count"])

        group = grouped.setdefault(
            ecosystem_name,
            {
                "ecosystem": ecosystem_name,
                "total": 0,
                "by_severity": {severity: 0 for severity in _SEVERITIES},
                "_latest_values": [],
            },
        )
        group["total"] += count
        group["by_severity"][severity_name] += count
        total += count

        if row["latest_published_at"] is not None:
            group["_latest_values"].append(row["latest_published_at"])

    groups: List[Dict[str, Any]] = []
    for group in grouped.values():
        latest_values = group.pop("_latest_values")
        latest = max(latest_values) if latest_values else None
        group["latest_published_at"] = _published_at_string(latest)
        groups.append(group)

    groups.sort(key=lambda item: (-item["total"], item["ecosystem"]))
    groups = groups[:limit]

    return {"total": total, "groups": groups}


get_vuln_advisory_ecosystem_breakdown = get_ecosystem_breakdown


def _self_test() -> None:
    assert VulnAdvisory.__tablename__ == "vuln_advisories"

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestingSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    with engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE vuln_advisories (
                    id INTEGER PRIMARY KEY,
                    feed TEXT,
                    summary TEXT,
                    severity TEXT,
                    ecosystem TEXT,
                    package TEXT,
                    affected_ranges TEXT,
                    aliases TEXT,
                    source_url TEXT,
                    published_at TIMESTAMP,
                    fetched_at TIMESTAMP,
                    identities TEXT,
                    content_hash TEXT
                )
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO vuln_advisories
                    (feed, summary, severity, ecosystem, package,
                     affected_ranges, aliases, source_url, published_at,
                     fetched_at, identities, content_hash)
                VALUES
                    ('test', 'npm advisory', 'critical', 'npm', 'example-npm',
                     '>=1.0.0', 'TEST-NPM-1', 'https://example.test/npm',
                     '2024-01-03T00:00:00', '2024-01-03T00:00:00',
                     '{}', 'hash-npm'),
                    ('test', 'pypi advisory', 'high', 'pypi', 'example-pypi',
                     '>=2.0.0', 'TEST-PYPI-1', 'https://example.test/pypi',
                     '2024-01-02T00:00:00', '2024-01-02T00:00:00',
                     '{}', 'hash-pypi'),
                    ('test', 'advisory without severity', NULL, 'npm', 'example-unknown',
                     '>=3.0.0', 'TEST-NPM-2', 'https://example.test/unknown',
                     '2024-01-01T00:00:00', '2024-01-01T00:00:00',
                     '{}', 'hash-unknown')
                """
            )
        )

    def override_get_session():
        with TestingSessionLocal() as session:
            yield session

    local_app = FastAPI()
    local_app.dependency_overrides[get_session] = override_get_session

    @local_app.get("/api/vuln/advisories/ecosystem")
    def ecosystem_endpoint(
        ecosystem: Optional[str] = Query(default=None),
        limit: int = Query(default=20, ge=1, le=100),
        db: Session = Depends(get_session),
    ):
        return get_ecosystem_breakdown(ecosystem=ecosystem, limit=limit, db=db)

    from fastapi.testclient import TestClient

    with TestClient(local_app) as client:
        response = client.get("/api/vuln/advisories/ecosystem")

    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] == 3
    assert sum(
        group["by_severity"]["unknown"] for group in payload["groups"]
    ) == 1
    totals = [group["total"] for group in payload["groups"]]
    assert totals == sorted(totals, reverse=True)
    assert payload["groups"][0]["ecosystem"] == "npm"
    assert payload["groups"][0]["total"] == 2

    engine.dispose()
    print("PASS")


if __name__ == "__main__":
    _self_test()