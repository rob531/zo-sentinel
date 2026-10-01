# deps: fastapi, pydantic, sqlalchemy
from datetime import datetime, timezone
from typing import Annotated, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry

router = APIRouter(prefix="/api", tags=["server_axis_score_timeline_api"])

AXIS_NAMES = frozenset(
    "overall_risk auth_strength capability_breadth data_sensitivity "
    "network_egress maintainer_trust exploit_surface".split()
)


class AxisScoreEntry(BaseModel):
    scored_at: datetime
    axis_name: str
    label: str
    label_index: int
    p_top: float
    p_critical: float
    p_danger: float
    model_version: str
    escalated: bool
    escalated_to: Optional[str]

    model_config = ConfigDict(from_attributes=True)


class ServerAxisTimelineResponse(BaseModel):
    server_id: str
    server_name: Optional[str]
    total: int
    limit: int
    offset: int
    timeline: list[AxisScoreEntry]


class AxisRollupEntry(BaseModel):
    axis_name: str
    label: str
    label_index: int
    p_top: float
    p_critical: float
    p_danger: float
    model_version: str
    scored_at: datetime
    escalated: bool
    escalated_to: Optional[str]


class ServerAxisLatestRollupResponse(BaseModel):
    server_id: str
    server_name: Optional[str]
    axes: list[AxisRollupEntry]


def _date_range(days: int):
    """Return (start, end) UTC datetimes for a lookback window."""
    now = datetime.now(timezone.utc)
    end = now.replace(second=0, microsecond=0)
    # subtract days by converting to ordinal (simple, no dateutil needed)
    start_ordinal = end.toordinal() - days
    start = datetime.fromordinal(start_ordinal).replace(
        hour=end.hour, minute=end.minute, second=0, microsecond=0, tzinfo=timezone.utc
    )
    return start, end


@router.get(
    "/servers/{server_id}/axis-timeline",
    response_model=ServerAxisTimelineResponse,
)
def get_server_axis_timeline(
    server_id: str,
    days: Annotated[int, Query(ge=1, le=365)] = 30,
    limit: Annotated[int, Query(ge=1, le=500)] = 200,
    offset: Annotated[int, Query(ge=0)] = 0,
    axis_name: Annotated[Optional[str], Query(description="Filter by axis name")] = None,
    session: Session = Depends(get_session),
) -> ServerAxisTimelineResponse:
    """Return a paginated axis-score timeline for a server, optionally filtered by days and axis."""
    start, end = _date_range(days)

    server = session.get(McpServerRegistry, server_id)
    if not server:
        raise HTTPException(status_code=404, detail=f"Server '{server_id}' not found")

    filters = [
        McpLlmAxisScore.server_id == server_id,
        McpLlmAxisScore.scored_at >= start,
        McpLlmAxisScore.scored_at <= end,
    ]
    if axis_name:
        if axis_name not in AXIS_NAMES:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid axis_name '{axis_name}'. Must be one of: {', '.join(sorted(AXIS_NAMES))}",
            )
        filters.append(McpLlmAxisScore.axis_name == axis_name)

    total = session.scalar(
        select(func.count()).select_from(McpLlmAxisScore).where(*filters)
    ) or 0

    stmt = (
        select(McpLlmAxisScore)
        .where(*filters)
        .order_by(McpLlmAxisScore.scored_at.desc())
        .limit(limit)
        .offset(offset)
    )
    rows = session.execute(stmt).scalars().all()

    timeline = [
        AxisScoreEntry(
            scored_at=row.scored_at,
            axis_name=row.axis_name,
            label=row.label,
            label_index=row.label_index,
            p_top=row.p_top,
            p_critical=row.p_critical,
            p_danger=row.p_danger,
            model_version=row.model_version,
            escalated=row.escalated,
            escalated_to=row.escalated_to,
        )
        for row in rows
    ]

    return ServerAxisTimelineResponse(
        server_id=server_id,
        server_name=server.name,
        total=total,
        limit=limit,
        offset=offset,
        timeline=timeline,
    )


@router.get(
    "/servers/{server_id}/axis-latest",
    response_model=ServerAxisLatestRollupResponse,
)
def get_server_axis_latest(
    server_id: str,
    session: Session = Depends(get_session),
) -> ServerAxisLatestRollupResponse:
    """Return the most recent axis-score row for each of the 7 axes for a server."""
    server = session.get(McpServerRegistry, server_id)
    if not server:
        raise HTTPException(status_code=404, detail=f"Server '{server_id}' not found")

    subq = (
        select(
            McpLlmAxisScore.axis_name,
            func.max(McpLlmAxisScore.scored_at).label("max_scored_at"),
        )
        .where(McpLlmAxisScore.server_id == server_id)
        .group_by(McpLlmAxisScore.axis_name)
        .subquery()
    )

    stmt = (
        select(McpLlmAxisScore)
        .join(
            subq,
            (McpLlmAxisScore.axis_name == subq.c.axis_name)
            & (McpLlmAxisScore.scored_at == subq.c.max_scored_at),
        )
        .where(McpLlmAxisScore.server_id == server_id)
    )
    rows = session.execute(stmt).scalars().all()

    axes = [
        AxisRollupEntry(
            axis_name=row.axis_name,
            label=row.label,
            label_index=row.label_index,
            p_top=row.p_top,
            p_critical=row.p_critical,
            p_danger=row.p_danger,
            model_version=row.model_version,
            scored_at=row.scored_at,
            escalated=row.escalated,
            escalated_to=row.escalated_to,
        )
        for row in rows
    ]

    return ServerAxisLatestRollupResponse(
        server_id=server_id,
        server_name=server.name,
        axes=axes,
    )


@router.get(
    "/servers/{server_id}/axis-timeline/{axis_name}",
    response_model=ServerAxisTimelineResponse,
)
def get_server_single_axis_timeline(
    server_id: str,
    axis_name: str,
    days: Annotated[int, Query(ge=1, le=365)] = 30,
    limit: Annotated[int, Query(ge=1, le=500)] = 200,
    offset: Annotated[int, Query(ge=0)] = 0,
    session: Session = Depends(get_session),
) -> ServerAxisTimelineResponse:
    """Return a paginated timeline for a single axis of a server."""
    if axis_name not in AXIS_NAMES:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid axis_name '{axis_name}'. Must be one of: {', '.join(sorted(AXIS_NAMES))}",
        )
    return get_server_axis_timeline(
        server_id=server_id,
        days=days,
        limit=limit,
        offset=offset,
        axis_name=axis_name,
        session=session,
    )


if __name__ == "__main__":
    from fastapi import FastAPI
    from sqlalchemy import StaticPool, create_engine
    from sqlalchemy.orm import sessionmaker

    from app.models import Base

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine)
    session = SessionLocal()

    now = datetime(2024, 1, 15, 12, 0, 0, tzinfo=timezone.utc)
    day_ago = datetime(2024, 1, 14, 12, 0, 0, tzinfo=timezone.utc)

    session.add_all([
        McpServerRegistry(
            server_id="srv-001",
            name="Test Server Alpha",
            registry_source="test",
            url="http://example.com",
            description="A test server",
            first_seen=now,
            last_seen=now,
            last_scanned=now,
            last_assessed=now,
            scan_count=2,
            confidence=0.9,
            trust_score=0.7,
            verdict="active",
            verdict_reasoning="Test",
            risk_tier="medium",
            meta={},
        ),
        McpLlmAxisScore(
            id=1, server_id="srv-001", axis_name="overall_risk", label="elevated",
            p_top=0.15, p_critical=0.05, p_danger=0.35, model_version="v2",
            scored_at=day_ago, adapter_sha256="sha256abc", label_index=2, escalated=False, probs=[],
        ),
        McpLlmAxisScore(
            id=2, server_id="srv-001", axis_name="overall_risk", label="normal",
            p_top=0.60, p_critical=0.01, p_danger=0.12, model_version="v2",
            scored_at=now, adapter_sha256="sha256abc", label_index=1, escalated=False, probs=[],
        ),
        McpLlmAxisScore(
            id=3, server_id="srv-001", axis_name="data_sensitivity", label="high",
            p_top=0.10, p_critical=0.30, p_danger=0.45, model_version="v2",
            scored_at=now, adapter_sha256="sha256abc", label_index=3, escalated=True, escalated_to="critical", probs=[],
        ),
    ])
    session.commit()

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = lambda: session

    from fastapi.testclient import TestClient

    client = TestClient(app)

    # --- happy path: full timeline ---
    resp = client.get("/api/servers/srv-001/axis-timeline?days=30")
    assert resp.status_code == 200, f"axis-timeline failed: {resp.text}"
    data = resp.json()
    assert data["server_id"] == "srv-001"
    assert data["server_name"] == "Test Server Alpha"
    assert data["total"] == 3, f"Expected 3 rows, got {data['total']}"
    assert len(data["timeline"]) == 3
    axis_names = {e["axis_name"] for e in data["timeline"]}
    assert "overall_risk" in axis_names
    assert "data_sensitivity" in axis_names

    # --- happy path: single-axis filter ---
    resp2 = client.get("/api/servers/srv-001/axis-timeline/overall_risk?days=30")
    assert resp2.status_code == 200
    data2 = resp2.json()
    assert all(e["axis_name"] == "overall_risk" for e in data2["timeline"])

    # --- happy path: axis-latest ---
    resp3 = client.get("/api/servers/srv-001/axis-latest")
    assert resp3.status_code == 200
    data3 = resp3.json()
    assert len(data3["axes"]) == 2  # only two distinct axes seeded
    axes_by_name = {a["axis_name"]: a for a in data3["axes"]}
    assert axes_by_name["overall_risk"]["label"] == "normal"   # most recent row
    assert axes_by_name["data_sensitivity"]["escalated"] is True

    # --- 404 for unknown server ---
    resp4 = client.get("/api/servers/nonexistent/axis-timeline")
    assert resp4.status_code == 404, f"Expected 404, got {resp4.status_code}"

    # --- 400 for invalid axis_name ---
    resp5 = client.get("/api/servers/srv-001/axis-timeline/invalid_axis")
    assert resp5.status_code == 400, f"Expected 400, got {resp5.status_code}"

    # --- pagination ---
    resp6 = client.get("/api/servers/srv-001/axis-timeline?limit=1&offset=0")
    assert resp6.status_code == 200
    data6 = resp6.json()
    assert len(data6["timeline"]) == 1
    assert data6["total"] == 3

    print("PASS")
