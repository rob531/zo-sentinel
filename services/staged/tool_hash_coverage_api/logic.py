from typing import List, Optional

from fastapi import Depends, Query
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry


_REGISTRY_TABLE_NAME = McpServerRegistry.__tablename__

if _REGISTRY_TABLE_NAME != "mcp_server_registry":
    raise RuntimeError("Unexpected registry model table")


class UncoveredServer(BaseModel):
    server_id: str
    name: Optional[str] = None
    registry_source: Optional[str] = None


class RegistrySourceCoverage(BaseModel):
    registry_source: Optional[str] = None
    total: int
    covered: int


class ToolHashCoverageResponse(BaseModel):
    total_servers: int
    covered_servers: int
    coverage_ratio: float
    uncovered_sample: List[UncoveredServer]
    by_registry_source: List[RegistrySourceCoverage]


_COVERAGE_SQL = text(
    """
    SELECT
        COUNT(*) AS total_servers,
        COALESCE(
            SUM(CASE WHEN hashes.server_id IS NOT NULL THEN 1 ELSE 0 END),
            0
        ) AS covered_servers
    FROM mcp_server_registry AS registry
    LEFT JOIN (
        SELECT DISTINCT server_id
        FROM mcp_tool_hashes
    ) AS hashes ON hashes.server_id = registry.server_id
    WHERE (
        CAST(:registry_source AS TEXT) IS NULL
        OR registry.registry_source = CAST(:registry_source AS TEXT)
    )
    """
)

_BY_REGISTRY_SOURCE_SQL = text(
    """
    SELECT
        registry.registry_source AS registry_source,
        COUNT(*) AS total,
        COALESCE(
            SUM(CASE WHEN hashes.server_id IS NOT NULL THEN 1 ELSE 0 END),
            0
        ) AS covered
    FROM mcp_server_registry AS registry
    LEFT JOIN (
        SELECT DISTINCT server_id
        FROM mcp_tool_hashes
    ) AS hashes ON hashes.server_id = registry.server_id
    WHERE (
        CAST(:registry_source AS TEXT) IS NULL
        OR registry.registry_source = CAST(:registry_source AS TEXT)
    )
    GROUP BY registry.registry_source
    ORDER BY registry.registry_source
    """
)

_UNCOVERED_SQL = text(
    """
    SELECT
        registry.server_id AS server_id,
        registry.name AS name,
        registry.registry_source AS registry_source
    FROM mcp_server_registry AS registry
    LEFT JOIN (
        SELECT DISTINCT server_id
        FROM mcp_tool_hashes
    ) AS hashes ON hashes.server_id = registry.server_id
    WHERE hashes.server_id IS NULL
      AND (
        CAST(:registry_source AS TEXT) IS NULL
        OR registry.registry_source = CAST(:registry_source AS TEXT)
    )
    ORDER BY registry.server_id
    LIMIT :sample_limit
    """
)


def get_tool_hash_coverage(
    registry_source: Optional[str] = Query(None),
    limit: int = Query(100, ge=1, le=1000),
    session: Session = Depends(get_session),
) -> ToolHashCoverageResponse:
    parameters = {"registry_source": registry_source}
    summary = session.execute(_COVERAGE_SQL, parameters).mappings().one()

    total_servers = int(summary["total_servers"] or 0)
    covered_servers = int(summary["covered_servers"] or 0)
    coverage_ratio = (
        covered_servers / total_servers if total_servers else 0.0
    )

    by_registry_source = [
        RegistrySourceCoverage(
            registry_source=row["registry_source"],
            total=int(row["total"] or 0),
            covered=int(row["covered"] or 0),
        )
        for row in session.execute(
            _BY_REGISTRY_SOURCE_SQL, parameters
        ).mappings()
    ]

    sample_limit = min(limit, 100)
    uncovered_sample = [
        UncoveredServer(
            server_id=row["server_id"],
            name=row["name"],
            registry_source=row["registry_source"],
        )
        for row in session.execute(
            _UNCOVERED_SQL,
            {
                **parameters,
                "sample_limit": sample_limit,
            },
        ).mappings()
    ]

    return ToolHashCoverageResponse(
        total_servers=total_servers,
        covered_servers=covered_servers,
        coverage_ratio=coverage_ratio,
        uncovered_sample=uncovered_sample,
        by_registry_source=by_registry_source,
    )


if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestingSessionLocal = sessionmaker(bind=test_engine)

    with test_engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE mcp_server_registry (
                    server_id TEXT NOT NULL PRIMARY KEY,
                    name TEXT,
                    registry_source TEXT,
                    first_seen TEXT
                )
                """
            )
        )
        connection.execute(
            text(
                """
                CREATE TABLE mcp_tool_hashes (
                    server_id TEXT
                )
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO mcp_server_registry
                    (server_id, name, registry_source, first_seen)
                VALUES
                    ('server-1', 'Server One', 'npm', '2025-01-01'),
                    ('server-2', 'Server Two', 'npm', '2025-01-02'),
                    ('server-3', 'Server Three', 'pypi', '2025-01-03'),
                    ('server-4', 'Server Four', 'pypi', '2025-01-04')
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO mcp_tool_hashes (server_id)
                VALUES ('server-1')
                """
            )
        )

    def override_get_session():
        database_session = TestingSessionLocal()
        try:
            yield database_session
        finally:
            database_session.close()

    that_app = FastAPI()
    that_app.dependency_overrides[get_session] = override_get_session

    @that_app.get(
        "/api/fingerprints/tool-hash-coverage",
        response_model=ToolHashCoverageResponse,
    )
    def tool_hash_coverage_endpoint(
        registry_source: Optional[str] = Query(None),
        limit: int = Query(100, ge=1, le=1000),
        session: Session = Depends(get_session),
    ):
        return get_tool_hash_coverage(
            registry_source=registry_source,
            limit=limit,
            session=session,
        )

    with TestClient(that_app) as client:
        response = client.get("/api/fingerprints/tool-hash-coverage")
        assert response.status_code == 200
        payload = response.json()
        assert payload["total_servers"] == 4
        assert payload["covered_servers"] == 1
        assert payload["coverage_ratio"] == 0.25
        assert len(payload["by_registry_source"]) == 2

    print("PASS")