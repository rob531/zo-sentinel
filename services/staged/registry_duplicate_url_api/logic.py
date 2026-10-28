from datetime import datetime, timezone
from urllib.parse import urlsplit, urlunsplit

from fastapi import Depends, FastAPI
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry


def _normalise_url(value: str | None) -> str:
    if not value:
        return ""

    candidate = value.strip()
    try:
        parsed = urlsplit(candidate)
        scheme = parsed.scheme.lower()
        netloc = parsed.netloc.lower()
        path = parsed.path.rstrip("/").lower()
        return urlunsplit((scheme, netloc, path, "", ""))
    except ValueError:
        return candidate.lower().rstrip("/")


def get_duplicate_urls(session: Session = Depends(get_session)) -> dict:
    result = session.execute(
        text(
            """
            WITH normalized AS (
                SELECT
                    server_id,
                    name,
                    registry_source,
                    url,
                    verdict,
                    risk_tier,
                    last_seen,
                    lower(rtrim(url, '/')) AS normalized_url
                FROM mcp_server_registry
            ),
            duplicate_groups AS (
                SELECT
                    normalized_url,
                    COUNT(*) AS server_count
                FROM normalized
                GROUP BY normalized_url
                HAVING COUNT(*) > 1
            )
            SELECT
                n.server_id,
                n.name,
                n.registry_source,
                n.url,
                n.verdict,
                n.risk_tier,
                n.last_seen,
                n.normalized_url,
                g.server_count
            FROM normalized AS n
            INNER JOIN duplicate_groups AS g
                ON g.normalized_url = n.normalized_url
            ORDER BY
                g.server_count DESC,
                n.normalized_url ASC,
                n.server_id ASC
            """
        )
    ).mappings().all()

    groups_by_url: dict[str, dict] = {}

    for row in result:
        normalised = _normalise_url(row["url"])
        group = groups_by_url.setdefault(
            normalised,
            {
                "url": normalised,
                "count": 0,
                "servers": [],
            },
        )
        group["count"] += 1
        group["servers"].append(
            {
                "server_id": row["server_id"],
                "name": row["name"],
                "registry_source": row["registry_source"],
                "risk_tier": row["risk_tier"],
                "verdict": row["verdict"],
                "last_seen": row["last_seen"],
            }
        )

    groups = list(groups_by_url.values())
    groups.sort(key=lambda item: (-item["count"], item["url"]))

    return {
        "generated_at": datetime.now(timezone.utc),
        "total_groups": len(groups),
        "duplicate_servers": sum(group["count"] for group in groups),
        "groups": groups,
    }


get_duplicate_url_groups = get_duplicate_urls
get_duplicate_servers = get_duplicate_urls
list_duplicate_urls = get_duplicate_urls
get_duplicate_url_data = get_duplicate_urls


if __name__ == "__main__":
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestSession = sessionmaker(bind=test_engine)

    with test_engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE mcp_server_registry (
                    server_id TEXT,
                    name TEXT,
                    registry_source TEXT,
                    url TEXT,
                    verdict TEXT,
                    risk_tier TEXT,
                    last_seen TEXT
                )
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO mcp_server_registry
                    (server_id, name, registry_source, url, verdict, risk_tier, last_seen)
                VALUES
                    ('one', 'First', 'source-a', 'HTTPS://Example.com/', 'ok', 'low', '2024-01-01'),
                    ('two', 'Second', 'source-b', 'https://example.com', 'ok', 'medium', '2024-01-02'),
                    ('three', 'Unique', 'source-c', 'https://unique.example.org/', 'ok', 'high', '2024-01-03')
                """
            )
        )

    test_session = TestSession()
    test_app = FastAPI()

    def override_get_session():
        return test_session

    test_app.dependency_overrides[get_session] = override_get_session

    @test_app.get("/api/registry/duplicate-urls")
    def duplicate_urls_endpoint(session: Session = Depends(get_session)):
        return get_duplicate_urls(session=session)

    from fastapi.testclient import TestClient

    response = TestClient(test_app).get("/api/registry/duplicate-urls")
    payload = response.json()

    assert response.status_code == 200
    assert payload["total_groups"] == 1
    assert payload["groups"][0]["count"] == 2
    assert payload["groups"][0]["url"] == "https://example.com"

    print("PASS")