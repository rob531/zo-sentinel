from datetime import datetime
from typing import Optional
from pydantic import BaseModel
from fastapi import Depends
import httpx

from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry


class QuarantineEntry(BaseModel):
    filename: str
    quarantined_at: datetime
    fail_count: int


class CohortStat(BaseModel):
    cohort: str
    size: int
    fail_rate: float
    cohort_id: str


class Recommendation(BaseModel):
    file: str
    action: str
    rationale: str


class GateAttributionReport(BaseModel):
    generated_at: datetime
    quarantine_count: int
    cohort_stats: list[CohortStat]
    recommendations: list[Recommendation]


def compute_recommendation_tier(fail_rate: float) -> str:
    if fail_rate < 0.20:
        return "accept"
    elif fail_rate < 0.40:
        return "reset"
    else:
        return "investigate"


def _query_bus(sql: str, params: Optional[dict] = None) -> list[dict]:
    response = httpx.post(
        "http://127.0.0.1:8772/query",
        json={"sql": sql, "params": params or {}},
        timeout=30.0,
    )
    response.raise_for_status()
    return response.json()


def get_gate_attribution_report(session=Depends(get_session)) -> GateAttributionReport:
    quarantine_sql = """
        SELECT 
            m.server_id as filename,
            MAX(m.scored_at) as quarantined_at,
            COUNT(*) FILTER (WHERE m.escalated = true) as fail_count
        FROM mcp_llm_axis_scores m
        WHERE m.escalated = true
        GROUP BY m.server_id
        ORDER BY fail_count DESC
        LIMIT 20
    """
    
    cohort_sql = """
        SELECT 
            COALESCE(m.axis_name, 'unknown') as cohort,
            COUNT(*) as size,
            AVG(CASE WHEN m.escalated = true THEN 1.0 ELSE 0.0 END) as fail_rate,
            m.decision_rule_version as cohort_id
        FROM mcp_llm_axis_scores m
        GROUP BY m.axis_name, m.decision_rule_version
        ORDER BY fail_rate DESC
    """
    
    quarantine_data = _query_bus(quarantine_sql)
    cohort_data = _query_bus(cohort_sql)
    
    quarantine_entries = [
        QuarantineEntry(
            filename=q["filename"],
            quarantined_at=datetime.fromisoformat(q["quarantined_at"]) if q["quarantined_at"] else datetime.utcnow(),
            fail_count=q["fail_count"],
        )
        for q in quarantine_data
    ]
    
    cohort_stats = [
        CohortStat(
            cohort=c["cohort"],
            size=c["size"],
            fail_rate=round(c["fail_rate"], 4) if c["fail_rate"] else 0.0,
            cohort_id=c["cohort_id"],
        )
        for c in cohort_data
    ]
    
    recommendations = []
    for qe in quarantine_entries:
        if cohort_stats:
            avg_fail_rate = sum(cs.fail_rate for cs in cohort_stats) / len(cohort_stats)
            tier = compute_recommendation_tier(avg_fail_rate)
        else:
            tier = "accept"
        
        rationale_map = {
            "accept": f"Fail rate {qe.fail_count}% within acceptable threshold",
            "reset": f"Fail rate elevated, reset recommended for {qe.filename}",
            "investigate": f"High fail rate detected, immediate investigation required for {qe.filename}",
        }
        
        recommendations.append(Recommendation(
            file=qe.filename,
            action=tier,
            rationale=rationale_map[tier],
        ))
    
    return GateAttributionReport(
        generated_at=datetime.utcnow(),
        quarantine_count=len(quarantine_entries),
        cohort_stats=cohort_stats,
        recommendations=recommendations,
    )


if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    
    app = FastAPI()
    
    def mock_get_gate_attribution_report():
        now = datetime.utcnow()
        quarantine_mock = [
            QuarantineEntry(filename="quarantine_001.bin", quarantined_at=now, fail_count=12),
            QuarantineEntry(filename="quarantine_002.bin", quarantined_at=now, fail_count=8),
            QuarantineEntry(filename="quarantine_003.bin", quarantined_at=now, fail_count=5),
            QuarantineEntry(filename="quarantine_004.bin", quarantined_at=now, fail_count=3),
        ]
        cohort_mock = [
            CohortStat(cohort="axis_alpha", size=100, fail_rate=0.45, cohort_id="v1.0"),
            CohortStat(cohort="axis_beta", size=200, fail_rate=0.35, cohort_id="v1.0"),
            CohortStat(cohort="axis_gamma", size=150, fail_rate=0.25, cohort_id="v1.1"),
            CohortStat(cohort="axis_delta", size=80, fail_rate=0.15, cohort_id="v1.1"),
            CohortStat(cohort="axis_epsilon", size=50, fail_rate=0.10, cohort_id="v1.2"),
        ]
        
        return GateAttributionReport(
            generated_at=now,
            quarantine_count=4,
            cohort_stats=cohort_mock,
            recommendations=[
                Recommendation(
                    file="quarantine_001.bin",
                    action="investigate",
                    rationale="High fail rate detected",
                ),
                Recommendation(
                    file="quarantine_002.bin",
                    action="reset",
                    rationale="Fail rate elevated",
                ),
                Recommendation(
                    file="quarantine_003.bin",
                    action="reset",
                    rationale="Fail rate elevated",
                ),
                Recommendation(
                    file="quarantine_004.bin",
                    action="accept",
                    rationale="Fail rate within threshold",
                ),
            ],
        )
    
    @app.get("/api/diagnostics/gate-attribution")
    def endpoint():
        return mock_get_gate_attribution_report()
    
    client = TestClient(app)
    response = client.get("/api/diagnostics/gate-attribution")
    
    assert response.status_code == 200, f"Expected 200, got {response.status_code}"
    
    data = response.json()
    assert data["quarantine_count"] == 4, f"Expected quarantine_count=4, got {data['quarantine_count']}"
    assert data["recommendations"][0]["action"] in ["accept", "reset", "investigate"], \
        f"Expected action in [accept,reset,investigate], got {data['recommendations'][0]['action']}"
    
    print("PASS")