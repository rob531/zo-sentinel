from typing import Any, Dict, List, Optional

from fastapi import Depends, FastAPI
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import VulnAdvisory, VulnLink


_BUCKET_COUNT = 10


def _confidence_bucket(value: Any) -> int:
    try:
        confidence = float(value)
    except (TypeError, ValueError):
        return 0

    if confidence < 0:
        return 0
    if confidence >= 1:
        return _BUCKET_COUNT - 1

    bucket = int(confidence * _BUCKET_COUNT)
    return min(max(bucket, 0), _BUCKET_COUNT - 1)


def get_match_confidence_histogram(
    session: Session = Depends(get_session),
) -> Dict[str, Any]:
    rows = session.execute(
        text(
            """
            SELECT
                vl.match_confidence AS match_confidence,
                vl.match_basis AS match_basis,
                va.severity AS severity
            FROM vuln_links AS vl
            INNER JOIN vuln_advisories AS va
                ON va.id = vl.advisory_id
            """
        )
    ).mappings().all()

    buckets = [
        {
            "bucket_lower": index / _BUCKET_COUNT,
            "bucket_upper": (index + 1) / _BUCKET_COUNT,
            "count": 0,
        }
        for index in range(_BUCKET_COUNT)
    ]

    match_basis_counts: Dict[Optional[str], int] = {}
    severity_counts: Dict[Optional[str], int] = {}

    for row in rows:
        bucket = _confidence_bucket(row["match_confidence"])
        buckets[bucket]["count"] += 1

        match_basis = row["match_basis"]
        severity = row["severity"]
        match_basis_counts[match_basis] = match_basis_counts.get(match_basis, 0) + 1
        severity_counts[severity] = severity_counts.get(severity, 0) + 1

    by_match_basis = [
        {"match_basis": match_basis, "count": count}
        for match_basis, count in sorted(
            match_basis_counts.items(),
            key=lambda item: (item[0] is None, str(item[0])),
        )
    ]

    by_severity = [
        {"severity": severity, "count": count}
        for severity, count in sorted(
            severity_counts.items(),
            key=lambda item: (item[0] is None, str(item[0])),
        )
    ]

    return {
        "total_links": len(rows),
        "buckets": buckets,
        "by_match_basis": by_match_basis,
        "by_severity": by_severity,
    }


def get_match_confidence_histogram_data(
    session: Session = Depends(get_session),
) -> Dict[str, Any]:
    return get_match_confidence_histogram(session)


def get_match_confidence_hist(
    session: Session = Depends(get_session),
) -> Dict[str, Any]:
    return get_match_confidence_histogram(session)


def get_histogram(
    session: Session = Depends(get_session),
) -> Dict[str, Any]:
    return get_match_confidence_histogram(session)


app = FastAPI()


@app.get("/api/vuln/links/match-confidence-histogram")
def match_confidence_histogram_endpoint(
    session: Session = Depends(get_session),
) -> Dict[str, Any]:
    return get_match_confidence_histogram(session)


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
    TestingSession = sessionmaker(bind=engine, expire_on_commit=False)

    with engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE vuln_advisories (
                    id INTEGER PRIMARY KEY,
                    severity VARCHAR(32)
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
                    match_basis VARCHAR(64),
                    match_value VARCHAR(256),
                    match_confidence REAL,
                    linked_at DATETIME
                )
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO vuln_advisories (id, severity)
                VALUES (1, 'high'), (2, 'critical')
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO vuln_links
                    (id, advisory_id, server_id, match_basis, match_value,
                     match_confidence, linked_at)
                VALUES
                    (1, 1, 101, 'cve', 'CVE-2024-0001', 0.05, CURRENT_TIMESTAMP),
                    (2, 1, 102, 'package', 'example-package', 0.35, CURRENT_TIMESTAMP),
                    (3, 2, 103, 'cve', 'CVE-2024-0002', 0.75, CURRENT_TIMESTAMP),
                    (4, 2, 104, 'package', 'other-package', 0.95, CURRENT_TIMESTAMP)
                """
            )
        )

    def override_get_session():
        db = TestingSession()
        try:
            yield db
        finally:
            db.close()

    that_app = FastAPI()
    that_app.dependency_overrides[get_session] = override_get_session
    that_app.get(
        "/api/vuln/links/match-confidence-histogram"
    )(match_confidence_histogram_endpoint)

    with TestClient(that_app) as client:
        response = client.get("/api/vuln/links/match-confidence-histogram")
        assert response.status_code == 200
        payload = response.json()
        assert payload["total_links"] == 4
        bucket = next(
            item
            for item in payload["buckets"]
            if item["bucket_lower"] == 0.7 and item["bucket_upper"] == 0.8
        )
        assert bucket["count"] == 1

    print("PASS")


if __name__ == "__main__":
    _run_self_test()