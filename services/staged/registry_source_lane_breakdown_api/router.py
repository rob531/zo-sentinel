from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends
from fastapi.testclient import TestClient
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session

from .logic import get_registry_source_lane_breakdown

router = APIRouter(prefix="/api", tags=["registry-source-lanes"])


class RegistrySourceLane(BaseModel):
    registry_source: str
    server_count: int
    scored_count: int
    unscored_count: int
    high_trust_count: int
    avg_trust: float | None
    newest_assessment: datetime | None


class RegistrySourceLaneBreakdownResponse(BaseModel):
    generated_at: datetime
    total_servers: int
    lanes: list[RegistrySourceLane]


def _normalize_lanes(result: Any) -> list[Mapping[str, Any]]:
    if isinstance(result, Mapping):
        result = result.get("lanes", [])
    return [dict(row) if isinstance(row, Mapping) else row for row in result]


@router.get(
    "/registry/source-lanes",
    response_model=RegistrySourceLaneBreakdownResponse,
    summary="Break down MCP registry servers by ingestion lane",
)
def registry_source_lane_breakdown(
    session: Session = Depends(get_session),
) -> RegistrySourceLaneBreakdownResponse:
    rows = get_registry_source_lane_breakdown(session)
    lanes = _normalize_lanes(rows)

    normalized: list[RegistrySourceLane] = []
    for lane in lanes:
        values = dict(lane)
        values["registry_source"] = values.get("registry_source") or "unknown"
        normalized.append(RegistrySourceLane.model_validate(values))

    return RegistrySourceLaneBreakdownResponse(
        generated_at=datetime.now(timezone.utc),
        total_servers=sum(lane.server_count for lane in normalized),
        lanes=normalized,
    )


def _run_self_test() -> None:
    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    with engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE mcp_server_registry (
                    server_id TEXT PRIMARY KEY,
                    name TEXT,
                    registry_source TEXT,
                    trust_score REAL,
                    verdict TEXT,
                    risk_tier TEXT,
                    last_assessed TEXT,
                    scan_count INTEGER,
                    meta TEXT
                )
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO mcp_server_registry (
                    server_id, name, registry_source, trust_score, verdict,
                    risk_tier, last_assessed, scan_count, meta
                ) VALUES
                    ('s1', 'One', 'github', 90.0, 'trusted', 'low',
                     '2024-01-01T00:00:00Z', 2, NULL),
                    ('s2', 'Two', 'github', NULL, NULL, 'unknown',
                     NULL, 0, NULL),
                    ('s3', 'Three', 'npm', 80.0, 'trusted', 'low',
                     '2024-01-02T00:00:00Z', 1, NULL),
                    ('s4', 'Four', NULL, 40.0, 'risky', 'high',
                     '2024-01-03T00:00:00Z', 1, NULL)
                """
            )
        )

    testing_session = sessionmaker(bind=engine, expire_on_commit=False)

    def override_get_session():
        with testing_session() as session:
            yield session

    from fastapi import FastAPI

    test_app = FastAPI()
    test_app.include_router(router)
    test_app.dependency_overrides[get_session] = override_get_session

    with TestClient(test_app) as client:
        response = client.get("/api/registry/source-lanes")
        assert response.status_code == 200, response.text

        payload = response.json()
        assert len(payload["lanes"]) == 3, payload
        lanes = {lane["registry_source"]: lane for lane in payload["lanes"]}
        assert "unknown" in lanes, payload
        assert lanes["github"]["server_count"] == 2, payload
        assert lanes["github"]["unscored_count"] == 1, payload
        assert payload["total_servers"] == 4, payload

    engine.dispose()
    print("PASS")


if __name__ == "__main__":
    _run_self_test()