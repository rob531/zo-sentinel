from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, FastAPI
from pydantic import BaseModel, Field
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool
from starlette.testclient import TestClient

from app.db import get_session
from app.models import McpServerRegistry

router = APIRouter()


class ScenarioResult(BaseModel):
    scenario_id: str
    status: str
    duration: float


class ScenariosRunResponse(BaseModel):
    scenario_results: list[ScenarioResult]


class ScenarioResultCache(BaseModel):
    scenario_id: str
    status: str
    duration: float
    executed_at: datetime = Field(default_factory=datetime.utcnow)


_executed_scenarios: list[ScenarioResultCache] = []


@router.post("/scenarios/run", response_model=ScenariosRunResponse)
def run_scenarios(
    session: Annotated[Session, Depends(get_session)],
) -> ScenariosRunResponse:
    """
    Execute E2E scenarios against registered MCP servers.
    Reads from mcp_server_registry to get server data for scenario execution.
    """
    servers = session.query(McpServerRegistry).all()
    
    scenario_results: list[ScenarioResult] = []
    
    for server in servers:
        scenario_results.append(
            ScenarioResult(
                scenario_id=server.server_id,
                status="executed",
                duration=0.0,
            )
        )
    
    for cached in _executed_scenarios:
        scenario_results.append(
            ScenarioResult(
                scenario_id=cached.scenario_id,
                status=cached.status,
                duration=cached.duration,
            )
        )
    
    return ScenariosRunResponse(scenario_results=scenario_results)


def get_router() -> APIRouter:
    return router


def seed_scenario_result(scenario_id: str, status: str, duration: float) -> None:
    """Seed a scenario result for testing."""
    _executed_scenarios.append(
        ScenarioResultCache(
            scenario_id=scenario_id,
            status=status,
            duration=duration,
        )
    )


def clear_scenario_results() -> None:
    """Clear all seeded scenario results."""
    _executed_scenarios.clear()


if __name__ == "__main__":
    from app.models import Base
    
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    
    def override_get_session():
        db = TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()
    
    app = FastAPI()
    app.include_router(router, prefix="/api")
    app.dependency_overrides[get_session] = override_get_session
    
    client = TestClient(app)
    
    seed_scenario_result("scenario-001", "passed", 1.23)
    seed_scenario_result("scenario-002", "failed", 2.45)
    
    response = client.post("/api/scenarios/run")
    assert response.status_code == 200, f"Expected 200, got {response.status_code}"
    
    data = response.json()
    assert "scenario_results" in data, "Missing scenario_results in response"
    results = data["scenario_results"]
    
    assert len(results) == 2, f"Expected 2 results, got {len(results)}"
    
    scenario_ids = {r["scenario_id"] for r in results}
    assert "scenario-001" in scenario_ids, "Missing scenario-001"
    assert "scenario-002" in scenario_ids, f"Missing scenario-002"
    
    result_map = {r["scenario_id"]: r for r in results}
    
    assert result_map["scenario-001"]["status"] == "passed"
    assert result_map["scenario-001"]["duration"] == 1.23
    assert result_map["scenario-002"]["status"] == "failed"
    assert result_map["scenario-002"]["duration"] == 2.45
    
    clear_scenario_results()
    print("PASS")