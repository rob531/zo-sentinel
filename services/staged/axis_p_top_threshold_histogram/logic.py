from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore


CANONICAL_AXES = (
    "overall_risk",
    "auth_strength",
    "capability_breadth",
    "data_sensitivity",
    "network_egress",
    "maintainer_trust",
    "exploit_surface",
)

router = APIRouter(prefix="/api")


def _bucket_index(value: Any) -> int:
    if value is None:
        return -1
    try:
        number = float(value)
    except (TypeError, ValueError):
        return -1
    if number != number:
        return -1
    if number <= 0.0:
        return 0
    if number >= 1.0:
        return 9
    return min(9, int(number * 10.0))


def get_axis_p_top_histogram(
    axis: str = Query(..., description="Canonical LLM risk axis"),
    bins: int = Query(10, description="Number of fixed-width histogram bins"),
    db: Session = Depends(get_session),
) -> dict[str, Any]:
    if axis not in CANONICAL_AXES:
        raise HTTPException(
            status_code=422,
            detail=(
                "axis must be one of: " + ", ".join(CANONICAL_AXES)
            ),
        )
    if bins != 10:
        raise HTTPException(
            status_code=422,
            detail="bins must be 10 because the histogram uses fixed 0.1-wide bins",
        )

    statement = select(
        McpLlmAxisScore.p_top,
        McpLlmAxisScore.escalated,
        McpLlmAxisScore.decision_rule_version,
    ).where(McpLlmAxisScore.axis_name == axis)

    rows = db.execute(statement).all()
    counts = [0 for _ in range(10)]
    escalated = 0
    rule_versions: set[str] = set()

    for row in rows:
        index = _bucket_index(row.p_top)
        if index >= 0:
            counts[index] += 1
        if bool(row.escalated):
            escalated += 1
        if row.decision_rule_version is not None:
            rule_versions.add(str(row.decision_rule_version))

    return {
        "axis": axis,
        "bins": [
            {
                "lo": round(index / 10.0, 1),
                "hi": round((index + 1) / 10.0, 1),
                "count": counts[index],
            }
            for index in range(10)
        ],
        "escalated": escalated,
        "rule_versions": len(rule_versions),
        "total": len(rows),
    }


get_p_top_histogram = get_axis_p_top_histogram
axis_p_top_histogram = get_axis_p_top_histogram


@router.get("/axis/p-top-histogram")
def axis_p_top_histogram_endpoint(
    axis: str = Query(..., description="Canonical LLM risk axis"),
    bins: int = Query(10, description="Number of fixed-width histogram bins"),
    db: Session = Depends(get_session),
) -> dict[str, Any]:
    return get_axis_p_top_histogram(axis=axis, bins=bins, db=db)


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
    McpLlmAxisScore.__table__.create(bind=test_engine)
    TestSession = sessionmaker(bind=test_engine, expire_on_commit=False)
    test_db = TestSession()

    seed_rows = [
        McpLlmAxisScore(
            id=1,
            adapter_sha256="adapter-1",
            axis_name="exploit_surface",
            model_version="model-1",
            server_id="server-1",
            p_top=0.05,
            escalated=False,
            decision_rule_version="v1",
        ),
        McpLlmAxisScore(
            id=2,
            adapter_sha256="adapter-2",
            axis_name="exploit_surface",
            model_version="model-1",
            server_id="server-2",
            p_top=0.5,
            escalated=True,
            decision_rule_version="v1",
        ),
        McpLlmAxisScore(
            id=3,
            adapter_sha256="adapter-3",
            axis_name="exploit_surface",
            model_version="model-1",
            server_id="server-3",
            p_top=0.95,
            escalated=False,
            decision_rule_version="v2",
        ),
        McpLlmAxisScore(
            id=4,
            adapter_sha256="adapter-4",
            axis_name="overall_risk",
            model_version="model-1",
            server_id="server-4",
            p_top=0.05,
            escalated=False,
            decision_rule_version="v1",
        ),
        McpLlmAxisScore(
            id=5,
            adapter_sha256="adapter-5",
            axis_name="overall_risk",
            model_version="model-1",
            server_id="server-5",
            p_top=0.5,
            escalated=True,
            decision_rule_version="v2",
        ),
        McpLlmAxisScore(
            id=6,
            adapter_sha256="adapter-6",
            axis_name="overall_risk",
            model_version="model-1",
            server_id="server-6",
            p_top=0.95,
            escalated=False,
            decision_rule_version="v3",
        ),
    ]
    test_db.add_all(seed_rows)
    test_db.commit()

    test_app = FastAPI()
    test_app.include_router(router)

    def override_get_session():
        yield test_db

    test_app.dependency_overrides[get_session] = override_get_session
    client = TestClient(test_app)

    response = client.get(
        "/api/axis/p-top-histogram",
        params={"axis": "exploit_surface", "bins": 10},
    )
    assert response.status_code == 200
    payload = response.json()
    assert len(payload["bins"]) == 10
    assert payload["bins"][0]["count"] == 1
    assert payload["bins"][5]["count"] == 1
    assert payload["bins"][9]["count"] == 1
    assert payload["escalated"] == 1
    assert payload["rule_versions"] == 2
    assert payload["total"] == 3
    print("PASS")