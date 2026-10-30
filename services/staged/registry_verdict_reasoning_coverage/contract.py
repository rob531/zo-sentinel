from typing import List, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.db import get_session
from app.models import McpServerRegistry

router = APIRouter(prefix="/api")


class RegistrySourceCoverage(BaseModel):
    registry_source: str
    total: int
    with_verdict: int
    coverage_ratio: float


class RegistryVerdictCoverageResponse(BaseModel):
    total_servers: int
    with_verdict: int
    with_reasoning: int
    with_confidence: int
    with_risk_tier: int
    coverage_ratio: float
    by_registry_source: List[RegistrySourceCoverage]


def _is_present(value: object) -> bool:
    return value is not None and not (
        isinstance(value, str) and not value.strip()
    )


@router.get(
    "/registry/verdict-coverage",
    response_model=RegistryVerdictCoverageResponse,
)
def get_registry_verdict_coverage(
    registry_source: Optional[str] = None,
    db=Depends(get_session),
) -> RegistryVerdictCoverageResponse:
    query = db.query(McpServerRegistry)
    if registry_source is not None:
        query = query.filter(
            McpServerRegistry.registry_source == registry_source
        )

    servers = query.all()

    with_verdict = sum(
        1 for server in servers if _is_present(server.verdict)
    )
    with_reasoning = sum(
        1 for server in servers if _is_present(server.verdict_reasoning)
    )
    with_confidence = sum(
        1 for server in servers if _is_present(server.confidence)
    )
    with_risk_tier = sum(
        1 for server in servers if _is_present(server.risk_tier)
    )

    source_groups = {}
    for server in servers:
        source = server.registry_source
        source_name = (
            str(source)
            if _is_present(source)
            else "unknown"
        )
        group = source_groups.setdefault(
            source_name,
            {"total": 0, "with_verdict": 0},
        )
        group["total"] += 1
        if _is_present(server.verdict):
            group["with_verdict"] += 1

    by_registry_source = [
        RegistrySourceCoverage(
            registry_source=source_name,
            total=values["total"],
            with_verdict=values["with_verdict"],
            coverage_ratio=(
                values["with_verdict"] / values["total"]
                if values["total"]
                else 0.0
            ),
        )
        for source_name, values in sorted(source_groups.items())
    ]

    total_servers = len(servers)
    return RegistryVerdictCoverageResponse(
        total_servers=total_servers,
        with_verdict=with_verdict,
        with_reasoning=with_reasoning,
        with_confidence=with_confidence,
        with_risk_tier=with_risk_tier,
        coverage_ratio=(
            with_verdict / total_servers if total_servers else 0.0
        ),
        by_registry_source=by_registry_source,
    )


def _self_test() -> None:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    McpServerRegistry.__table__.create(engine)

    with Session(engine) as session:
        session.add_all(
            [
                McpServerRegistry(
                    server_id="fully-populated",
                    registry_source="source-a",
                    verdict="malicious",
                    verdict_reasoning="Confirmed by policy analysis",
                    confidence=0.95,
                    risk_tier="high",
                ),
                McpServerRegistry(
                    server_id="verdict-only",
                    registry_source="source-b",
                    verdict="benign",
                ),
                McpServerRegistry(
                    server_id="reasoning-only",
                    registry_source="source-c",
                    verdict_reasoning="Registry reasoning",
                ),
                McpServerRegistry(
                    server_id="empty",
                    registry_source="source-d",
                ),
            ]
        )
        session.commit()

    def override_get_session():
        with Session(engine) as session:
            yield session

    test_app = FastAPI()
    test_app.include_router(router)
    test_app.dependency_overrides[get_session] = override_get_session

    with TestClient(test_app) as client:
        response = client.get("/api/registry/verdict-coverage")

    assert response.status_code == 200
    payload = response.json()
    assert payload["total_servers"] == 4
    assert payload["with_verdict"] == 2
    assert payload["with_reasoning"] == 2
    assert payload["coverage_ratio"] == 0.5
    assert len(payload["by_registry_source"]) == 4
    assert {
        entry["registry_source"]
        for entry in payload["by_registry_source"]
    } == {"source-a", "source-b", "source-c", "source-d"}

    engine.dispose()
    print("PASS")


if __name__ == "__main__":
    _self_test()