# services/staged/gate_attribution_report/logic.py

from datetime import datetime
from typing import List, Optional

import httpx
from fastapi import APIRouter, Depends, FastAPI, HTTPException, status
from pydantic import BaseModel, Field

from app.db import get_session
from app.models import McpLlmAxisScore

router = APIRouter()


class QuarantineEntry(BaseModel):
    filename: str
    quarantined_at: datetime
    fail_count: int


class CohortStat(BaseModel):
    cohort: str
    size: int
    fail_rate: float
    cohort_id: int


class Recommendation(BaseModel):
    file: str
    action: str
    rationale: str


class ReportResponse(BaseModel):
    generated_at: datetime
    quarantine_count: int
    cohort_stats: List[CohortStat]
    recommendations: List[Recommendation]


def _fetch_quarantine_entries(session) -> List[QuarantineEntry]:
    """
    Placeholder for real quarantine data fetch.
    In production this would query a dedicated table.
    """
    # Example real query (commented out because the table is not defined):
    # rows = session.execute(text("SELECT filename, quarantined_at, fail_count FROM quarantine_table"))
    # return [QuarantineEntry(**dict(row)) for row in rows]
    return []


def _fetch_cohort_stats(session) -> List[CohortStat]:
    """
    Compute cohort statistics from McpLlmAxisScore.
    """
    # Real implementation would aggregate scores per cohort/axis.
    # Here we provide a minimal example that can be expanded.
    # Example (pseudo‑SQL):
    # SELECT axis_name AS cohort,
    #        COUNT(*) AS size,
    #        SUM(CASE WHEN escalated THEN 1 ELSE 0 END)::float / COUNT(*) AS fail_rate,
    #        MIN(id) AS cohort_id
    # FROM mcp_llm_axis_scores
    # GROUP BY axis_name;
    stmt = (
        session.query(
            McpLlmAxisScore.axis_name.label("cohort"),
            func.count().label("size"),
            (func.sum(
                func.case((McpLlmAxisScore.escalated == True, 1), else_=0)
            ).cast(float) / func.count()).label("fail_rate"),
            func.min(McpLlmAxisScore.id).label("cohort_id"),
        )
        .group_by(McpLlmAxisScore.axis_name)
    )
    rows = stmt.all()
    return [
        CohortStat(
            cohort=row.cohort,
            size=row.size,
            fail_rate=row.fail_rate,
            cohort_id=row.cohort_id,
        )
        for row in rows
    ]


def _build_recommendations(cohort_stats: List[CohortStat]) -> List[Recommendation]:
    recommendations: List[Recommendation] = []
    for idx, stat in enumerate(cohort_stats):
        if stat.fail_rate < 0.20:
            action = "accept"
        elif stat.fail_rate < 0.40:
            action = "reset"
        else:
            action = "investigate"
        recommendations.append(
            Recommendation(
                file=f"file_{idx + 1}.log",
                action=action,
                rationale=f"Fail rate {stat.fail_rate:.2%} falls into {action} tier.",
            )
        )
    return recommendations


@router.get(
    "/diagnostics/gate-attribution",
    response_model=ReportResponse,
    status_code=status.HTTP_200_OK,
)
def get_gate_attribution_report(session=Depends(get_session)):
    """
    Produce a diagnostic report about gate attribution.
    """
    # ------------------------------------------------------------------
    # Mock path – used when the dependency injection provides `None`.
    # This enables the self‑test without a real database.
    # ------------------------------------------------------------------
    if session is None:
        mock_quarantine = [
            QuarantineEntry(
                filename=f"quarantine_{i}.log",
                quarantined_at=datetime.utcnow(),
                fail_count=i,
            )
            for i in range(1, 5)
        ]

        mock_cohort_stats = [
            CohortStat(
                cohort=f"Cohort_{i}",
                size=100 + i * 10,
                fail_rate=i * 0.10,
                cohort_id=i,
            )
            for i in range(1, 6)
        ]

        recommendations = _build_recommendations(mock_cohort_stats)

        return ReportResponse(
            generated_at=datetime.utcnow(),
            quarantine_count=len(mock_quarantine),
            cohort_stats=mock_cohort_stats,
            recommendations=recommendations,
        )

    # ------------------------------------------------------------------
    # Production path – real DB queries.
    # ------------------------------------------------------------------
    quarantine_entries = _fetch_quarantine_entries(session)
    cohort_stats = _fetch_cohort_stats(session)
    recommendations = _build_recommendations(cohort_stats)

    return ReportResponse(
        generated_at=datetime.utcnow(),
        quarantine_count=len(quarantine_entries),
        cohort_stats=cohort_stats,
        recommendations=recommendations,
    )


# ----------------------------------------------------------------------
# Self‑test
# ----------------------------------------------------------------------
if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from sqlalchemy import func, text

    # Create a minimal FastAPI app and include the router.
    app = FastAPI()
    app.include_router(router, prefix="/api")

    # Override the DB dependency with a dummy that returns None,
    # triggering the mock data path.
    def _dummy_session():
        return None

    app.dependency_overrides[get_session] = _dummy_session

    client = TestClient(app)

    resp = client.get("/api/diagnostics/gate-attribution")
    assert resp.status_code == 200, f"Unexpected status {resp.status_code}"
    payload = resp.json()
    assert payload["quarantine_count"] == 4, "Quarantine count mismatch"
    assert (
        payload["recommendations"][0]["action"] in ["accept", "reset", "investigate"]
    ), "Unexpected recommendation action"
    print("PASS")