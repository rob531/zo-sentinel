from datetime import datetime, timezone
from typing import Dict, List

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry


class RegistrySourceSummary(BaseModel):
    registry_source: str
    count: int
    critical_count: int


class VerdictTierMatrixResponse(BaseModel):
    generated_at: datetime
    total_servers: int
    by_verdict: Dict[str, int]
    by_risk_tier: Dict[str, int]
    matrix: List[Dict[str, object]]
    by_registry_source: List[RegistrySourceSummary]


router = APIRouter(prefix="/api")


def build_verdict_tier_matrix(db: Session) -> VerdictTierMatrixResponse:
    matrix_rows = db.execute(
        select(
            McpServerRegistry.verdict,
            McpServerRegistry.risk_tier,
            func.count().label("count"),
        )
        .group_by(
            McpServerRegistry.verdict,
            McpServerRegistry.risk_tier,
        )
        .order_by(
            McpServerRegistry.verdict,
            McpServerRegistry.risk_tier,
        )
    ).all()

    matrix: List[Dict[str, object]] = []
    by_verdict: Dict[str, int] = {}
    by_risk_tier: Dict[str, int] = {}
    total_servers = 0

    for row in matrix_rows:
        verdict = row.verdict if row.verdict is not None else "UNKNOWN"
        risk_tier = row.risk_tier if row.risk_tier is not None else "UNKNOWN"
        count = int(row.count)

        matrix.append(
            {
                "verdict": verdict,
                "risk_tier": risk_tier,
                "count": count,
            }
        )
        by_verdict[verdict] = by_verdict.get(verdict, 0) + count
        by_risk_tier[risk_tier] = by_risk_tier.get(risk_tier, 0) + count
        total_servers += count

    source_rows = db.execute(
        select(
            McpServerRegistry.registry_source,
            func.count().label("count"),
            func.sum(
                case(
                    (McpServerRegistry.risk_tier == "HIGH", 1),
                    else_=0,
                )
            ).label("critical_count"),
        )
        .group_by(McpServerRegistry.registry_source)
        .order_by(McpServerRegistry.registry_source)
    ).all()

    by_registry_source = [
        RegistrySourceSummary(
            registry_source=(
                row.registry_source
                if row.registry_source is not None
                else "UNKNOWN"
            ),
            count=int(row.count),
            critical_count=int(row.critical_count or 0),
        )
        for row in source_rows
    ]

    return VerdictTierMatrixResponse(
        generated_at=datetime.now(timezone.utc),
        total_servers=total_servers,
        by_verdict=by_verdict,
        by_risk_tier=by_risk_tier,
        matrix=matrix,
        by_registry_source=by_registry_source,
    )


@router.get(
    "/registry/verdict-tier-matrix",
    response_model=VerdictTierMatrixResponse,
)
def get_verdict_tier_matrix(
    db: Session = Depends(get_session),
) -> VerdictTierMatrixResponse:
    return build_verdict_tier_matrix(db)


get_registry_verdict_tier_matrix = get_verdict_tier_matrix


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
    McpServerRegistry.__table__.create(test_engine)
    TestSession = sessionmaker(bind=test_engine, autoflush=False)

    assessed_at = datetime.now(timezone.utc)
    fixture = [
        ("server-1", "TRUSTED_GENERAL", "LOW", "catalog"),
        ("server-2", "HIGH_RISK_ISOLATED", "MEDIUM", "catalog"),
        ("server-3", "TRUSTED_GENERAL", "HIGH", "catalog"),
        ("server-4", "HIGH_RISK_ISOLATED", "LOW", "manual"),
        ("server-5", "TRUSTED_GENERAL", "MEDIUM", "manual"),
        ("server-6", "HIGH_RISK_ISOLATED", "HIGH", "manual"),
    ]

    with TestSession() as session:
        for server_id, verdict, risk_tier, registry_source in fixture:
            session.add(
                McpServerRegistry(
                    server_id=server_id,
                    name=server_id,
                    url=f"https://example.test/{server_id}",
                    verdict=verdict,
                    risk_tier=risk_tier,
                    registry_source=registry_source,
                    last_assessed=assessed_at,
                )
            )
        session.commit()

    def override_get_session():
        with TestSession() as session:
            yield session

    test_app = FastAPI()
    test_app.dependency_overrides[get_session] = override_get_session
    test_app.include_router(router)

    with TestClient(test_app) as client:
        response = client.get("/api/registry/verdict-tier-matrix")
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["total_servers"] == 6
        assert sum(item["count"] for item in payload["matrix"]) == 6
        assert len(payload["by_registry_source"]) == 2

    test_engine.dispose()
    print("PASS")