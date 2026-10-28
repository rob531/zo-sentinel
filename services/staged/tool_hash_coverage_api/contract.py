from typing import List, Optional

from fastapi import APIRouter, Depends, FastAPI, Query
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry


router = APIRouter(prefix="/api")


class UncoveredServer(BaseModel):
    server_id: str
    name: str
    registry_source: str


class RegistrySourceCoverage(BaseModel):
    registry_source: str
    total: int
    covered: int


class ToolHashCoverageResponse(BaseModel):
    total_servers: int
    covered_servers: int
    coverage_ratio: float
    uncovered_sample: List[UncoveredServer]
    by_registry_source: List[RegistrySourceCoverage]


@router.get(
    "/fingerprints/tool-hash-coverage",
    response_model=ToolHashCoverageResponse,
)
def tool_hash_coverage(
    registry_source: Optional[str] = Query(default=None),
    limit: int = Query(default=100, ge=1, le=1000),
    session: Session = Depends(get_session),
) -> ToolHashCoverageResponse:
    sample_limit = min(limit, 100)

    coverage_sql = text(
        """
        SELECT
            registry.registry_source,
            COUNT(DISTINCT registry.server_id) AS total,
            COUNT(
                DISTINCT CASE
                    WHEN hashes.server_id IS NOT NULL THEN registry.server_id
                END
            ) AS covered
        FROM mcp_server_registry AS registry
        LEFT JOIN (
            SELECT DISTINCT server_id
            FROM mcp_tool_hashes
        ) AS hashes
            ON hashes.server_id = registry.server_id
        WHERE (:registry_source IS NULL OR registry.registry_source = :registry_source)
        GROUP BY registry.registry_source
        ORDER BY registry.registry_source
        """
    )

    rows = session.execute(
        coverage_sql,
        {"registry_source": registry_source},
    ).mappings().all()

    by_registry_source = [
        RegistrySourceCoverage(
            registry_source=row["registry_source"] or "",
            total=int(row["total"]),
            covered=int(row["covered"]),
        )
        for row in rows
    ]

    total_servers = sum(item.total for item in by_registry_source)
    covered_servers = sum(item.covered for item in by_registry_source)
    coverage_ratio = (
        float(covered_servers) / float(total_servers)
        if total_servers
        else 0.0
    )

    uncovered_sql = text(
        """
        SELECT
            registry.server_id,
            registry.name,
            registry.registry_source
        FROM mcp_server_registry AS registry
        LEFT JOIN (
            SELECT DISTINCT server_id
            FROM mcp_tool_hashes
        ) AS hashes
            ON hashes.server_id = registry.server_id
        WHERE hashes.server_id IS NULL
          AND (:registry_source IS NULL OR registry.registry_source = :registry_source)
        ORDER BY registry.server_id
        LIMIT :sample_limit
        """
    )

    uncovered_rows = session.execute(
        uncovered_sql,
        {
            "registry_source": registry_source,
            "sample_limit": sample_limit,
        },
    ).mappings().all()

    uncovered_sample = [
        UncoveredServer(
            server_id=row["server_id"],
            name=row["name"] or "",
            registry_source=row["registry_source"] or "",
        )
        for row in uncovered_rows
    ]

    return ToolHashCoverageResponse(
        total_servers=total_servers,
        covered_servers=covered_servers,
        coverage_ratio=coverage_ratio,
        uncovered_sample=uncovered_sample,
        by_registry_source=by_registry_source,
    )


app = FastAPI()
app.include_router(router)


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
    TestingSession = sessionmaker(bind=engine)

    with engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE mcp_server_registry (
                    server_id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    registry_source TEXT NOT NULL,
                    first_seen TEXT
                )
                """
            )
        )
        connection.execute(
            text(
                """
                CREATE TABLE mcp_tool_hashes (
                    id INTEGER PRIMARY KEY,
                    server_id TEXT NOT NULL
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
                    ('srv-1', 'Server One', 'npm', '2024-01-01'),
                    ('srv-2', 'Server Two', 'npm', '2024-01-02'),
                    ('srv-3', 'Server Three', 'pypi', '2024-01-03'),
                    ('srv-4', 'Server Four', 'pypi', '2024-01-04')
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO mcp_tool_hashes (id, server_id)
                VALUES (1, 'srv-1')
                """
            )
        )

    db_session = TestingSession()

    def override_get_session():
        yield db_session

    that_app = FastAPI()
    that_app.include_router(router)
    that_app.dependency_overrides[get_session] = override_get_session

    with TestClient(that_app) as client:
        response = client.get("/api/fingerprints/tool-hash-coverage")
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["total_servers"] == 4, payload
        assert payload["covered_servers"] == 1, payload
        assert payload["coverage_ratio"] == 0.25, payload
        assert len(payload["by_registry_source"]) == 2, payload

    db_session.close()
    engine.dispose()
    print("PASS")


if __name__ == "__main__":
    _run_self_test()