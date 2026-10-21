from datetime import datetime, timezone
from typing import Dict, List, Optional

from fastapi import APIRouter, Depends, FastAPI, Query
from pydantic import BaseModel
from sqlalchemy import and_, create_engine, select, text
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry


AXIS_NAMES = (
    "overall_risk",
    "auth_strength",
    "capability_breadth",
    "data_sensitivity",
    "network_egress",
    "maintainer_trust",
    "exploit_surface",
)


class AxisCoverageCount(BaseModel):
    scored: int
    missing: int


class AxisCoverageGap(BaseModel):
    server_id: str
    risk_tier: Optional[str]
    missing_axes: List[str]
    last_scored: Optional[datetime]


class AxisCoverageGapsResponse(BaseModel):
    total_servers: int
    servers_with_all_axes: int
    axis_counts: Dict[str, AxisCoverageCount]
    top_gaps: List[AxisCoverageGap]


router = APIRouter(prefix="/api")


@router.get(
    "/axis/coverage-gaps",
    response_model=AxisCoverageGapsResponse,
)
def axis_coverage_gaps(
    limit: int = Query(50, ge=0),
    tier: Optional[str] = Query(None),
    session: Session = Depends(get_session),
) -> AxisCoverageGapsResponse:
    statement = (
        select(
            McpServerRegistry.server_id,
            McpServerRegistry.risk_tier,
            McpLlmAxisScore.axis_name,
            McpLlmAxisScore.scored_at,
        )
        .select_from(McpServerRegistry)
        .outerjoin(
            McpLlmAxisScore,
            and_(
                McpLlmAxisScore.server_id == McpServerRegistry.server_id,
                McpLlmAxisScore.axis_name.in_(AXIS_NAMES),
            ),
        )
        .order_by(McpServerRegistry.server_id)
    )
    if tier is not None:
        statement = statement.where(McpServerRegistry.risk_tier == tier)

    rows = session.execute(statement).all()

    servers: Dict[str, Dict[str, object]] = {}
    for server_id, risk_tier, axis_name, scored_at in rows:
        server = servers.setdefault(
            server_id,
            {
                "risk_tier": risk_tier,
                "axes": set(),
                "last_scored": None,
            },
        )
        if axis_name is not None:
            server["axes"].add(axis_name)
            if scored_at is not None:
                current = server["last_scored"]
                if current is None or _timestamp_key(scored_at) > _timestamp_key(current):
                    server["last_scored"] = scored_at

    axis_counts = {
        axis_name: AxisCoverageCount(
            scored=sum(axis_name in server["axes"] for server in servers.values()),
            missing=sum(axis_name not in server["axes"] for server in servers.values()),
        )
        for axis_name in AXIS_NAMES
    }

    gaps: List[AxisCoverageGap] = []
    for server_id, server in servers.items():
        missing_axes = [axis_name for axis_name in AXIS_NAMES if axis_name not in server["axes"]]
        if missing_axes:
            gaps.append(
                AxisCoverageGap(
                    server_id=server_id,
                    risk_tier=server["risk_tier"],
                    missing_axes=missing_axes,
                    last_scored=server["last_scored"],
                )
            )

    gaps.sort(key=lambda gap: (-len(gap.missing_axes), gap.server_id))
    effective_limit = min(limit, 200)

    return AxisCoverageGapsResponse(
        total_servers=len(servers),
        servers_with_all_axes=sum(
            len(server["axes"]) == len(AXIS_NAMES) for server in servers.values()
        ),
        axis_counts=axis_counts,
        top_gaps=gaps[:effective_limit],
    )


def _timestamp_key(value: datetime) -> datetime:
    if value.tzinfo is not None:
        return value.astimezone(timezone.utc).replace(tzinfo=None)
    return value


def create_app() -> FastAPI:
    contract_app = FastAPI(title="Axis Coverage Gap API")
    contract_app.include_router(router)
    return contract_app


app = create_app()


if __name__ == "__main__":
    from fastapi.testclient import TestClient

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE mcp_server_registry (
                    server_id TEXT PRIMARY KEY,
                    risk_tier TEXT,
                    last_seen TIMESTAMP
                )
                """
            )
        )
        connection.execute(
            text(
                """
                CREATE TABLE mcp_llm_axis_scores (
                    server_id TEXT,
                    axis_name TEXT,
                    scored_at TIMESTAMP
                )
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO mcp_server_registry (server_id, risk_tier, last_seen)
                VALUES
                    ('server-all', 'gold', CURRENT_TIMESTAMP),
                    ('server-partial', 'silver', CURRENT_TIMESTAMP),
                    ('server-unscored', 'bronze', CURRENT_TIMESTAMP)
                """
            )
        )
        for axis_name in AXIS_NAMES:
            connection.execute(
                text(
                    """
                    INSERT INTO mcp_llm_axis_scores (server_id, axis_name, scored_at)
                    VALUES (:server_id, :axis_name, CURRENT_TIMESTAMP)
                    """
                ),
                {"server_id": "server-all", "axis_name": axis_name},
            )
        for axis_name in AXIS_NAMES[:3]:
            connection.execute(
                text(
                    """
                    INSERT INTO mcp_llm_axis_scores (server_id, axis_name, scored_at)
                    VALUES (:server_id, :axis_name, CURRENT_TIMESTAMP)
                    """
                ),
                {"server_id": "server-partial", "axis_name": axis_name},
            )

    def override_get_session():
        with Session(engine) as session:
            yield session

    that_app = FastAPI()
    that_app.include_router(router)
    that_app.dependency_overrides[get_session] = override_get_session

    with TestClient(that_app) as client:
        response = client.get("/api/axis/coverage-gaps", params={"limit": 50})
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["total_servers"] == 3, payload
        assert payload["servers_with_all_axes"] == 1, payload
        assert set(payload["axis_counts"]) == set(AXIS_NAMES), payload
        assert payload["axis_counts"]["overall_risk"] == {
            "scored": 2,
            "missing": 1,
        }, payload
        unscored = next(
            gap for gap in payload["top_gaps"] if gap["server_id"] == "server-unscored"
        )
        assert len(unscored["missing_axes"]) == 7, payload
        assert set(unscored["missing_axes"]) == set(AXIS_NAMES), payload

    print("PASS")