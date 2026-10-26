from __future__ import annotations

from typing import Any

from fastapi import Depends
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import VulnAdvisory


_VULN_ADVISORIES_TABLE = VulnAdvisory.__tablename__

_FEED_ROLLUP_SQL = text(
    f"""
    SELECT
        feed,
        COUNT(*) AS advisory_count,
        MAX(published_at) AS latest_published_at,
        MIN(published_at) AS oldest_published_at,
        SUM(CASE WHEN LOWER(severity) = 'low' THEN 1 ELSE 0 END) AS low_count,
        SUM(CASE WHEN LOWER(severity) = 'medium' THEN 1 ELSE 0 END) AS medium_count,
        SUM(CASE WHEN LOWER(severity) = 'high' THEN 1 ELSE 0 END) AS high_count,
        SUM(CASE WHEN LOWER(severity) = 'critical' THEN 1 ELSE 0 END) AS critical_count,
        COUNT(DISTINCT ecosystem) AS ecosystem_count
    FROM {_VULN_ADVISORIES_TABLE}
    GROUP BY feed
    ORDER BY advisory_count DESC, feed ASC
    """
)

_TOTALS_SQL = text(
    f"""
    SELECT
        COUNT(*) AS advisories,
        COUNT(DISTINCT feed) AS feeds
    FROM {_VULN_ADVISORIES_TABLE}
    """
)


def get_feed_grouping(
    session: Session = Depends(get_session),
    limit: int | None = None,
) -> dict[str, Any]:
    if limit is not None and limit < 0:
        raise ValueError("limit must be non-negative")

    query = str(_FEED_ROLLUP_SQL)
    parameters: dict[str, Any] = {}
    if limit is not None:
        query += " LIMIT :limit"
        parameters["limit"] = limit

    feed_rows = session.execute(text(query), parameters).mappings().all()
    totals_row = session.execute(_TOTALS_SQL).mappings().one()

    feeds = [
        {
            "feed": row["feed"],
            "count": int(row["advisory_count"]),
            "latest_published_at": row["latest_published_at"],
            "oldest_published_at": row["oldest_published_at"],
            "severity_mix": {
                "low": int(row["low_count"] or 0),
                "medium": int(row["medium_count"] or 0),
                "high": int(row["high_count"] or 0),
                "critical": int(row["critical_count"] or 0),
            },
            "ecosystem_count": int(row["ecosystem_count"] or 0),
        }
        for row in feed_rows
    ]

    return {
        "feeds": feeds,
        "totals": {
            "advisories": int(totals_row["advisories"] or 0),
            "feeds": int(totals_row["feeds"] or 0),
        },
    }


get_vuln_advisory_feed_grouping = get_feed_grouping
get_feed_rollup = get_feed_grouping


if __name__ == "__main__":
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
                    feed VARCHAR(255),
                    summary TEXT,
                    severity VARCHAR(32),
                    ecosystem VARCHAR(255),
                    package VARCHAR(255),
                    affected_ranges TEXT,
                    aliases TEXT,
                    source_url TEXT,
                    published_at DATETIME,
                    fetched_at DATETIME,
                    identities TEXT,
                    content_hash VARCHAR(255)
                )
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO vuln_advisories (
                    id, feed, summary, severity, ecosystem, package,
                    affected_ranges, aliases, source_url, published_at,
                    fetched_at, identities, content_hash
                ) VALUES
                    (
                        1, 'alpha', 'First alpha advisory', 'high', 'PyPI',
                        'alpha-one', '[]', '[]', 'https://example.test/1',
                        '2024-01-03T00:00:00', '2024-01-03T00:01:00',
                        '[]', 'hash-1'
                    ),
                    (
                        2, 'alpha', 'Second alpha advisory', 'low', 'npm',
                        'alpha-two', '[]', '[]', 'https://example.test/2',
                        '2024-01-02T00:00:00', '2024-01-02T00:01:00',
                        '[]', 'hash-2'
                    ),
                    (
                        3, 'beta', 'First beta advisory', 'medium', 'PyPI',
                        'beta-one', '[]', '[]', 'https://example.test/3',
                        '2024-01-04T00:00:00', '2024-01-04T00:01:00',
                        '[]', 'hash-3'
                    ),
                    (
                        4, 'beta', 'Second beta advisory', 'critical', 'npm',
                        'beta-two', '[]', '[]', 'https://example.test/4',
                        '2024-01-01T00:00:00', '2024-01-01T00:01:00',
                        '[]', 'hash-4'
                    )
                """
            )
        )

    this_app = FastAPI()

    def override_get_session():
        db = TestSession()
        try:
            yield db
        finally:
            db.close()

    this_app.dependency_overrides[get_session] = override_get_session

    @this_app.get("/api/vuln/advisories/feeds")
    def advisories_feeds(
        limit: int | None = None,
        session: Session = Depends(get_session),
    ):
        return get_feed_grouping(session=session, limit=limit)

    client = TestClient(this_app)
    response = client.get("/api/vuln/advisories/feeds")

    assert response.status_code == 200
    payload = response.json()
    assert len(payload["feeds"]) == 2
    assert payload["totals"]["advisories"] == 4
    assert any(feed["feed"] == "alpha" and feed["count"] == 2 for feed in payload["feeds"])

    print("PASS")