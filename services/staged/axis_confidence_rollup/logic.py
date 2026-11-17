from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore


AXIS_LABELS: Dict[str, str] = {
    "overall_risk": "Overall Risk",
    "auth_strength": "Authentication Strength",
    "capability_breadth": "Capability Breadth",
    "data_sensitivity": "Data Sensitivity",
    "network_egress": "Network Egress",
    "maintainer_trust": "Maintainer Trust",
    "exploit_surface": "Exploit Surface",
}


class AxisConfidenceEntry(BaseModel):
    axis_name: str
    label: str
    count: int
    mean_p_top: float
    mean_p_critical: float
    escalated_count: int
    coverage: float


class AxisConfidenceResponse(BaseModel):
    generated_at: str
    axes: List[AxisConfidenceEntry]


router = APIRouter(prefix="/risk", tags=["risk"])


def axis_confidence_rollup(
    db: Session, days: int = 30
) -> Dict[str, object]:
    """Calculate per-axis confidence statistics over the real score table."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    rows = db.execute(
        select(
            McpLlmAxisScore.server_id,
            McpLlmAxisScore.axis_name,
            McpLlmAxisScore.label,
            McpLlmAxisScore.p_top,
            McpLlmAxisScore.p_critical,
            McpLlmAxisScore.escalated,
        ).where(McpLlmAxisScore.scored_at >= cutoff)
    ).all()

    scored_servers = {row.server_id for row in rows if row.server_id is not None}
    denominator = len(scored_servers)
    grouped: Dict[str, Dict[str, object]] = {}

    for row in rows:
        if row.axis_name is None:
            continue
        entry = grouped.setdefault(
            row.axis_name,
            {
                "labels": [],
                "server_ids": set(),
                "count": 0,
                "p_top": [],
                "p_critical": [],
                "escalated_count": 0,
            },
        )
        entry["count"] = int(entry["count"]) + 1
        if row.server_id is not None:
            entry["server_ids"].add(row.server_id)
        if row.label:
            entry["labels"].append(row.label)
        if row.p_top is not None:
            entry["p_top"].append(float(row.p_top))
        if row.p_critical is not None:
            entry["p_critical"].append(float(row.p_critical))
        if row.escalated:
            entry["escalated_count"] = int(entry["escalated_count"]) + 1

    axes: List[AxisConfidenceEntry] = []
    ordered_names = [name for name in AXIS_LABELS if name in grouped]
    ordered_names.extend(sorted(name for name in grouped if name not in AXIS_LABELS))

    for axis_name in ordered_names:
        entry = grouped[axis_name]
        p_top_values = entry["p_top"]
        p_critical_values = entry["p_critical"]
        labels = entry["labels"]
        axis_label = (
            str(labels[0])
            if labels
            else AXIS_LABELS.get(axis_name, axis_name.replace("_", " ").title())
        )
        mean_p_top = (
            sum(p_top_values) / len(p_top_values) if p_top_values else 0.0
        )
        mean_p_critical = (
            sum(p_critical_values) / len(p_critical_values)
            if p_critical_values
            else 0.0
        )
        coverage = (
            len(entry["server_ids"]) / denominator if denominator else 0.0
        )
        axes.append(
            AxisConfidenceEntry(
                axis_name=axis_name,
                label=axis_label,
                count=int(entry["count"]),
                mean_p_top=float(mean_p_top),
                mean_p_critical=float(mean_p_critical),
                escalated_count=int(entry["escalated_count"]),
                coverage=float(coverage),
            )
        )

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "axes": axes,
    }


@router.get("/axis-confidence", response_model=AxisConfidenceResponse)
def get_axis_confidence(
    days: int = Query(default=30, ge=1),
    db: Session = Depends(get_session),
) -> Dict[str, object]:
    return axis_confidence_rollup(db, days=days)


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
    McpLlmAxisScore.__table__.create(test_engine)
    TestSession = sessionmaker(bind=test_engine)
    test_db = TestSession()

    now = datetime.now(timezone.utc)
    seeded_axes = ["overall_risk", "auth_strength"]
    row_id = 1
    for server_number in range(1, 4):
        for axis_name in seeded_axes:
            escalated = server_number == 2 and axis_name == "auth_strength"
            test_db.add(
                McpLlmAxisScore(
                    id=row_id,
                    server_id=f"server-{server_number}",
                    axis_name=axis_name,
                    label=AXIS_LABELS[axis_name],
                    model_version="self-test",
                    p_top=0.75 if not escalated else 0.9,
                    p_critical=0.25 if not escalated else 0.4,
                    escalated=escalated,
                    scored_at=now,
                )
            )
            row_id += 1
    test_db.commit()

    test_app = FastAPI()
    test_app.include_router(router, prefix="/api")

    def override_get_session():
        yield test_db

    test_app.dependency_overrides[get_session] = override_get_session
    response = TestClient(test_app).get("/api/risk/axis-confidence")

    assert response.status_code == 200
    payload = response.json()
    assert len(payload["axes"]) == 2
    assert {entry["axis_name"] for entry in payload["axes"]} == set(seeded_axes)
    assert sum(entry["escalated_count"] for entry in payload["axes"]) == 1
    assert all(0.0 <= entry["mean_p_top"] <= 1.0 for entry in payload["axes"])

    test_db.close()
    test_engine.dispose()
    print("PASS")