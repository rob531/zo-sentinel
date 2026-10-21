from collections.abc import Iterator

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry

router = APIRouter(prefix="/api")
REGISTRY_TABLE = McpServerRegistry.__tablename__


class FamilyRiskItem(BaseModel):
    canonical_family: str
    server_count: int
    risk_tier_counts: dict[str, int]
    avg_trust_score: float | None
    worst_server_id: str | None


class FamilyRiskRollupResponse(BaseModel):
    total_families: int
    families: list[FamilyRiskItem]


FAMILY_RISK_ROLLUP_SQL = f"""
WITH normalized AS (
    SELECT
        COALESCE(canonical_family, '(uncategorised)') AS canonical_family,
        server_id,
        risk_tier,
        trust_score
    FROM {REGISTRY_TABLE}
),
family_counts AS (
    SELECT
        canonical_family,
        COUNT(*) AS server_count,
        SUM(CASE WHEN risk_tier = 'HIGH' THEN 1 ELSE 0 END) AS high_count,
        SUM(CASE WHEN risk_tier = 'MEDIUM' THEN 1 ELSE 0 END) AS medium_count,
        SUM(CASE WHEN risk_tier = 'LOW' THEN 1 ELSE 0 END) AS low_count,
        SUM(CASE
            WHEN risk_tier IS NULL OR risk_tier NOT IN ('HIGH', 'MEDIUM', 'LOW')
            THEN 1 ELSE 0
        END) AS unknown_count,
        AVG(trust_score) AS avg_trust_score
    FROM normalized
    GROUP BY canonical_family
    HAVING COUNT(*) >= :min_servers
),
ranked_families AS (
    SELECT
        family_counts.*,
        COUNT(*) OVER () AS total_families
    FROM family_counts
),
worst_servers AS (
    SELECT
        canonical_family,
        server_id,
        ROW_NUMBER() OVER (
            PARTITION BY canonical_family
            ORDER BY
                CASE risk_tier
                    WHEN 'HIGH' THEN 0
                    WHEN 'MEDIUM' THEN 1
                    WHEN 'LOW' THEN 2
                    ELSE 3
                END,
                trust_score ASC NULLS LAST,
                server_id ASC
        ) AS row_number
    FROM normalized
)
SELECT
    ranked_families.canonical_family,
    ranked_families.server_count,
    ranked_families.high_count,
    ranked_families.medium_count,
    ranked_families.low_count,
    ranked_families.unknown_count,
    ranked_families.avg_trust_score,
    ranked_families.total_families,
    worst_servers.server_id AS worst_server_id
FROM ranked_families
LEFT JOIN worst_servers
    ON worst_servers.canonical_family = ranked_families.canonical_family
   AND worst_servers.row_number = 1
ORDER BY
    ranked_families.server_count DESC,
    ranked_families.canonical_family ASC
LIMIT :limit
"""


@router.get(
    "/registry/family-risk-rollup",
    response_model=FamilyRiskRollupResponse,
)
def get_family_risk_rollup(
    limit: int = Query(default=25),
    min_servers: int = Query(default=1),
    session: Session = Depends(get_session),
) -> FamilyRiskRollupResponse:
    effective_limit = max(1, min(200, limit))
    effective_min_servers = max(1, min(1000, min_servers))

    result = session.execute(
        text(FAMILY_RISK_ROLLUP_SQL),
        {
            "limit": effective_limit,
            "min_servers": effective_min_servers,
        },
    )

    rows = result.mappings().all()
    families = [
        FamilyRiskItem(
            canonical_family=str(row["canonical_family"]),
            server_count=int(row["server_count"]),
            risk_tier_counts={
                "HIGH": int(row["high_count"]),
                "MEDIUM": int(row["medium_count"]),
                "LOW": int(row["low_count"]),
                "UNKNOWN": int(row["unknown_count"]),
            },
            avg_trust_score=(
                float(row["avg_trust_score"])
                if row["avg_trust_score"] is not None
                else None
            ),
            worst_server_id=(
                str(row["worst_server_id"])
                if row["worst_server_id"] is not None
                else None
            ),
        )
        for row in rows
    ]

    return FamilyRiskRollupResponse(
        total_families=int(rows[0]["total_families"]) if rows else 0,
        families=families,
    )


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
    TestingSession = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    with engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE mcp_server_registry (
                    server_id TEXT PRIMARY KEY,
                    canonical_family TEXT,
                    risk_tier TEXT,
                    trust_score REAL,
                    name TEXT,
                    verdict TEXT
                )
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO mcp_server_registry
                    (server_id, canonical_family, risk_tier, trust_score, name, verdict)
                VALUES
                    ('server-1', 'acme/http-client', 'HIGH', 0.90, 'One', 'ok'),
                    ('server-2', 'acme/http-client', 'LOW', 0.70, 'Two', 'ok'),
                    ('server-3', 'acme/db-driver', 'MEDIUM', 0.80, 'Three', 'ok'),
                    ('server-4', NULL, NULL, NULL, 'Four', 'unknown')
                """
            )
        )

    def override_get_session() -> Iterator[Session]:
        with TestingSession() as session:
            yield session

    test_app = FastAPI()
    test_app.include_router(router)
    test_app.dependency_overrides[get_session] = override_get_session

    with TestClient(test_app) as client:
        response = client.get(
            "/api/registry/family-risk-rollup",
            params={"limit": 2, "min_servers": 1},
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["total_families"] == 3
        assert len(payload["families"]) == 2
        assert payload["families"][0]["canonical_family"] == "acme/http-client"
        assert payload["families"][0]["server_count"] == 2
        assert payload["families"][0]["risk_tier_counts"]["HIGH"] == 1
        assert payload["families"][0]["risk_tier_counts"]["LOW"] == 1
        assert isinstance(payload["families"][0]["avg_trust_score"], float)

        limited = client.get(
            "/api/registry/family-risk-rollup",
            params={"limit": 1},
        )
        assert limited.status_code == 200
        assert len(limited.json()["families"]) == 1

    engine.dispose()
    print("PASS")


if __name__ == "__main__":
    _self_test()