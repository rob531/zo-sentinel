from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Literal, Optional

from fastapi import APIRouter, Depends, FastAPI, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore


AxisName = Literal[
    "overall_risk",
    "auth_strength",
    "capability_breadth",
    "data_sensitivity",
    "network_egress",
    "maintainer_trust",
    "exploit_surface",
]

AXIS_NAMES = (
    "overall_risk",
    "auth_strength",
    "capability_breadth",
    "data_sensitivity",
    "network_egress",
    "maintainer_trust",
    "exploit_surface",
)


class AxisCoverage(BaseModel):
    axis_name: str
    label: Optional[str]
    server_count: int
    escalated_count: int
    mean_p_top: float
    mean_p_critical: float
    critical_share: float
    latest_scored_at: Optional[str]


class AxisCoverageResponse(BaseModel):
    axes: list[AxisCoverage]
    total_servers: int
    generated_at: str


def _isoformat(value: Optional[datetime]) -> Optional[str]:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.isoformat()


def _mean(values: list[Optional[float]]) -> float:
    present = [float(value) for value in values if value is not None]
    if not present:
        return 0.0
    return sum(present) / len(present)


def get_axis_coverage(
    session: Session,
    axis: Optional[str] = None,
    window_days: int = 30,
) -> dict:
    if axis is not None and axis not in AXIS_NAMES:
        raise ValueError(f"Unsupported axis: {axis}")
    if window_days < 1:
        raise ValueError("window_days must be at least 1")

    cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=window_days)
    statement = select(McpLlmAxisScore).where(McpLlmAxisScore.scored_at >= cutoff)
    if axis is not None:
        statement = statement.where(McpLlmAxisScore.axis_name == axis)

    rows = list(session.execute(statement).scalars())
    rows.sort(
        key=lambda row: (
            row.scored_at is not None,
            row.scored_at or datetime.min,
            row.id or 0,
        )
    )

    latest_by_server_axis: dict[tuple[str, str], McpLlmAxisScore] = {}
    for row in rows:
        latest_by_server_axis[(row.axis_name, row.server_id)] = row

    grouped: dict[str, list[McpLlmAxisScore]] = {}
    for (axis_name, _server_id), row in latest_by_server_axis.items():
        grouped.setdefault(axis_name, []).append(row)

    axes: list[AxisCoverage] = []
    all_servers: set[str] = set()

    for axis_name in AXIS_NAMES:
        score_rows = grouped.get(axis_name)
        if not score_rows:
            continue

        score_rows.sort(key=lambda row: (row.scored_at or datetime.min, row.id or 0))
        server_ids = {row.server_id for row in score_rows}
        all_servers.update(server_ids)

        labels = [row.label for row in score_rows if row.label is not None]
        label_counts = Counter(labels)
        label = None
        if label_counts:
            latest_label_at = {
                row.label: row.scored_at
                for row in score_rows
                if row.label is not None
            }
            label = max(
                label_counts,
                key=lambda value: (label_counts[value], latest_label_at.get(value) or datetime.min),
            )

        critical_count = sum(
            1
            for row in score_rows
            if row.label is not None and row.label.strip().casefold() == "critical"
        )
        latest_scored_at = max(
            (row.scored_at for row in score_rows if row.scored_at is not None),
            default=None,
        )

        axes.append(
            AxisCoverage(
                axis_name=axis_name,
                label=label,
                server_count=len(server_ids),
                escalated_count=sum(1 for row in score_rows if bool(row.escalated)),
                mean_p_top=_mean([row.p_top for row in score_rows]),
                mean_p_critical=_mean([row.p_critical for row in score_rows]),
                critical_share=critical_count / len(score_rows),
                latest_scored_at=_isoformat(latest_scored_at),
            )
        )

    return {
        "axes": [axis.model_dump() if hasattr(axis, "model_dump") else axis.dict() for axis in axes],
        "total_servers": len(all_servers),
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


router = APIRouter(prefix="/api")


@router.get("/axis/coverage", response_model=AxisCoverageResponse)
def axis_coverage(
    axis: Optional[AxisName] = Query(default=None),
    window_days: int = Query(default=30, ge=1, le=3650),
    session: Session = Depends(get_session),
) -> dict:
    return get_axis_coverage(session=session, axis=axis, window_days=window_days)


app = FastAPI()
app.include_router(router)


if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    McpLlmAxisScore.__table__.create(test_engine, checkfirst=True)
    TestSession = sessionmaker(bind=test_engine, expire_on_commit=False)

    scored_at = datetime.utcnow()
    with TestSession() as session:
        session.add_all(
            [
                McpLlmAxisScore(
                    id=1,
                    server_id="server-a",
                    axis_name="overall_risk",
                    model_version="test",
                    label="critical",
                    p_top=0.92,
                    p_critical=0.88,
                    escalated=True,
                    escalated_to="security",
                    scored_at=scored_at,
                ),
                McpLlmAxisScore(
                    id=2,
                    server_id="server-b",
                    axis_name="overall_risk",
                    model_version="test",
                    label="low",
                    p_top=0.71,
                    p_critical=0.12,
                    escalated=False,
                    scored_at=scored_at,
                ),
                McpLlmAxisScore(
                    id=3,
                    server_id="server-c",
                    axis_name="auth_strength",
                    model_version="test",
                    label="medium",
                    p_top=0.63,
                    p_critical=0.21,
                    escalated=False,
                    scored_at=scored_at,
                ),
            ]
        )
        session.commit()

    def override_get_session():
        with TestSession() as session:
            yield session

    that_app = FastAPI()
    that_app.include_router(router)
    that_app.dependency_overrides[get_session] = override_get_session

    response = TestClient(that_app).get("/api/axis/coverage?window_days=30")
    assert response.status_code == 200, response.text
    payload = response.json()
    assert len(payload["axes"]) == 2, payload
    overall = next(item for item in payload["axes"] if item["axis_name"] == "overall_risk")
    assert overall["server_count"] == 2, payload
    assert overall["escalated_count"] == 1, payload
    assert payload["total_servers"] == 3, payload
    print("PASS")