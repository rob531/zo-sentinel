from collections import defaultdict
from typing import Any

from fastapi import Depends, FastAPI
from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore

AXIS_NAMES = (
    "overall_risk",
    "auth_strength",
    "capability_breadth",
    "data_sensitivity",
    "network_egress",
    "maintainer_trust",
    "exploit_surface",
)


def get_axis_escalation_rate_by_axis(
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    axis_stmt = (
        select(
            McpLlmAxisScore.axis_name,
            func.count().label("total"),
            func.sum(
                case((McpLlmAxisScore.escalated.is_(True), 1), else_=0)
            ).label("escalated"),
        )
        .where(McpLlmAxisScore.axis_name.in_(AXIS_NAMES))
        .group_by(McpLlmAxisScore.axis_name)
    )
    axis_rows = session.execute(axis_stmt).all()

    totals_by_axis = {
        axis_name: {"total": 0, "escalated": 0}
        for axis_name in AXIS_NAMES
    }
    for row in axis_rows:
        totals_by_axis[row.axis_name] = {
            "total": int(row.total),
            "escalated": int(row.escalated or 0),
        }

    tier_stmt = (
        select(
            McpLlmAxisScore.axis_name,
            McpLlmAxisScore.escalated_to,
            func.count().label("count"),
        )
        .where(
            McpLlmAxisScore.axis_name.in_(AXIS_NAMES),
            McpLlmAxisScore.escalated.is_(True),
            McpLlmAxisScore.escalated_to.is_not(None),
        )
        .group_by(
            McpLlmAxisScore.axis_name,
            McpLlmAxisScore.escalated_to,
        )
    )
    tiers_by_axis: dict[str, dict[str, int]] = defaultdict(dict)
    for row in session.execute(tier_stmt).all():
        tiers_by_axis[row.axis_name][row.escalated_to] = int(row.count)

    axes: list[dict[str, Any]] = []
    total_rows = 0
    total_escalated = 0

    for axis_name in AXIS_NAMES:
        counts = totals_by_axis[axis_name]
        total = counts["total"]
        escalated = counts["escalated"]
        total_rows += total
        total_escalated += escalated
        axes.append(
            {
                "axis_name": axis_name,
                "total": total,
                "escalated": escalated,
                "rate": escalated / total if total else 0.0,
                "escalated_to": dict(tiers_by_axis.get(axis_name, {})),
            }
        )

    return {
        "axes": axes,
        "totals": {
            "rows": total_rows,
            "escalated": total_escalated,
        },
    }


get_axis_escalation_rate = get_axis_escalation_rate_by_axis
axis_escalation_rate_by_axis = get_axis_escalation_rate_by_axis


if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    test_engine = create_engine(
        "sqlite+pysqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestSessionLocal = sessionmaker(bind=test_engine, autoflush=False)

    with test_engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE mcp_llm_axis_scores (
                    axis_name VARCHAR NOT NULL,
                    escalated BOOLEAN NOT NULL,
                    escalated_to VARCHAR NULL
                )
                """
            )
        )

    escalated_counts = {
        "overall_risk": 0,
        "auth_strength": 2,
        "capability_breadth": 0,
        "data_sensitivity": 0,
        "network_egress": 0,
        "maintainer_trust": 0,
        "exploit_surface": 1,
    }
    seeded_rows = 0

    with TestSessionLocal() as session:
        for axis_name in AXIS_NAMES:
            for index in range(4):
                escalated = index < escalated_counts[axis_name]
                escalated_to = "tier_2" if escalated else None
                session.execute(
                    text(
                        """
                        INSERT INTO mcp_llm_axis_scores (
                            axis_name,
                            escalated,
                            escalated_to
                        ) VALUES (
                            :axis_name,
                            :escalated,
                            :escalated_to
                        )
                        """
                    ),
                    {
                        "axis_name": axis_name,
                        "escalated": escalated,
                        "escalated_to": escalated_to,
                    },
                )
                seeded_rows += 1
        session.commit()

    test_app = FastAPI()

    def override_get_session():
        session = TestSessionLocal()
        try:
            yield session
        finally:
            session.close()

    test_app.dependency_overrides[get_session] = override_get_session

    @test_app.get("/api/axis/escalation-rate")
    def axis_escalation_rate_endpoint(
        session: Session = Depends(get_session),
    ) -> dict[str, Any]:
        return get_axis_escalation_rate_by_axis(session=session)

    response = TestClient(test_app).get("/api/axis/escalation-rate")
    assert response.status_code == 200, response.text
    payload = response.json()
    assert len(payload["axes"]) == 7, payload
    assert payload["totals"]["rows"] == seeded_rows, payload
    assert payload["totals"]["escalated"] == 3, payload

    auth_strength = next(
        axis
        for axis in payload["axes"]
        if axis["axis_name"] == "auth_strength"
    )
    assert auth_strength["total"] == 4, payload
    assert auth_strength["escalated"] == 2, payload
    assert auth_strength["rate"] == 0.5, payload

    print("PASS")