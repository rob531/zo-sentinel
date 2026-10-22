from typing import Optional

from fastapi import APIRouter, Depends, FastAPI, Query
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import McpServerRegistry, VulnAdvisory, VulnLink


router = APIRouter(prefix="/api")


@router.get("/vuln/family-severity")
def family_severity(
    severity: Optional[str] = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    session=Depends(get_session),
):
    normalized_severity = severity.strip().lower() if severity else None
    if not normalized_severity:
        normalized_severity = None

    sql = """
    WITH severity_rows AS (
        SELECT
            vl.server_id,
            va.package,
            CASE
                WHEN LOWER(COALESCE(va.severity, '')) IN
                     ('critical', 'high', 'medium', 'low')
                THEN LOWER(COALESCE(va.severity, ''))
                ELSE 'unknown'
            END AS severity
        FROM vuln_links vl
        JOIN vuln_advisories va
          ON va.id = vl.advisory_id
    ),
    filtered_links AS (
        SELECT server_id, package, severity
        FROM severity_rows
        WHERE (:severity IS NULL OR severity = :severity)
    )
    SELECT
        ms.server_id AS server_id,
        ms.name AS server_name,
        ms.risk_tier AS risk_tier,
        SUM(CASE WHEN fl.severity = 'critical' THEN 1 ELSE 0 END) AS critical,
        SUM(CASE WHEN fl.severity = 'high' THEN 1 ELSE 0 END) AS high,
        SUM(CASE WHEN fl.severity = 'medium' THEN 1 ELSE 0 END) AS medium,
        SUM(CASE WHEN fl.severity = 'low' THEN 1 ELSE 0 END) AS low,
        SUM(CASE WHEN fl.severity = 'unknown' THEN 1 ELSE 0 END) AS unknown,
        COUNT(DISTINCT fl.package) AS distinct_packages,
        MAX(
            CASE fl.severity
                WHEN 'critical' THEN 5
                WHEN 'high' THEN 4
                WHEN 'medium' THEN 3
                WHEN 'low' THEN 2
                ELSE 1
            END
        ) AS max_rank
    FROM mcp_server_registry ms
    JOIN filtered_links fl
      ON fl.server_id = ms.server_id
    GROUP BY ms.server_id, ms.name, ms.risk_tier
    ORDER BY ms.server_id
    LIMIT :limit
    """

    rows = session.execute(
        text(sql),
        {
            "severity": normalized_severity,
            "limit": limit,
        },
    ).mappings().all()

    severity_names = {
        5: "critical",
        4: "high",
        3: "medium",
        2: "low",
        1: "unknown",
    }

    servers = []
    for row in rows:
        servers.append(
            {
                "server_id": str(row["server_id"]),
                "server_name": str(row["server_name"] or ""),
                "risk_tier": str(row["risk_tier"] or "unknown"),
                "by_severity": {
                    "critical": int(row["critical"] or 0),
                    "high": int(row["high"] or 0),
                    "medium": int(row["medium"] or 0),
                    "low": int(row["low"] or 0),
                    "unknown": int(row["unknown"] or 0),
                },
                "distinct_packages": int(row["distinct_packages"] or 0),
                "max_severity": severity_names.get(
                    int(row["max_rank"] or 1), "unknown"
                ),
            }
        )

    return {"servers": servers, "total_servers": len(servers)}


app = FastAPI(title="CVE Family Severity API")
app.include_router(router)


def _run_self_test() -> None:
    from sqlalchemy import create_engine

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)

    with engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE mcp_server_registry (
                    server_id TEXT PRIMARY KEY,
                    name TEXT,
                    risk_tier TEXT
                )
                """
            )
        )
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
                    published_at TEXT,
                    fetched_at TEXT,
                    identities TEXT,
                    content_hash TEXT
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
                    server_id TEXT,
                    match_basis TEXT,
                    match_value TEXT,
                    match_confidence REAL,
                    linked_at TEXT
                )
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO mcp_server_registry
                    (server_id, name, risk_tier)
                VALUES
                    ('server-critical', 'Critical Server', 'high'),
                    ('server-high', 'High Server', 'medium')
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO vuln_advisories
                    (id, feed, summary, severity, ecosystem, package,
                     affected_ranges, aliases, source_url, published_at,
                     fetched_at, identities, content_hash)
                VALUES
                    (1, 'test', 'critical advisory', 'critical', 'PyPI',
                     'critical-package', '[]', '[]', 'https://example.test/1',
                     '2024-01-01', '2024-01-01', '[]', 'hash-1'),
                    (2, 'test', 'high advisory', 'high', 'npm',
                     'high-package', '[]', '[]', 'https://example.test/2',
                     '2024-01-01', '2024-01-01', '[]', 'hash-2')
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO vuln_links
                    (advisory_id, server_id, match_basis, match_value,
                     match_confidence, linked_at)
                VALUES
                    (1, 'server-critical', 'package', 'critical-package',
                     0.99, '2024-01-01'),
                    (2, 'server-high', 'package', 'high-package',
                     0.95, '2024-01-01')
                """
            )
        )

    def override_get_session():
        with SessionLocal() as session:
            yield session

    that_app = FastAPI()
    that_app.include_router(router)
    that_app.dependency_overrides[get_session] = override_get_session

    with TestClient(that_app) as client:
        response = client.get("/api/vuln/family-severity")

    assert response.status_code == 200
    payload = response.json()
    assert payload["total_servers"] == 2

    servers = {server["server_id"]: server for server in payload["servers"]}
    assert set(servers) == {"server-critical", "server-high"}

    for server in servers.values():
        assert sum(server["by_severity"].values()) == 1

    assert servers["server-critical"]["max_severity"] == "critical"
    assert servers["server-high"]["max_severity"] == "high"

    print("PASS")


if __name__ == "__main__":
    _run_self_test()