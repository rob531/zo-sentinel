# deps: fastapi, sqlalchemy, pydantic
"""Scoring summary API -- per-day aggregation of axis scores with risk-tier breakdown."""

from datetime import date, datetime
from typing import Dict, List

from fastapi import APIRouter, Depends, FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel, Field
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.db import Base, get_session
from app.models import McpLlmAxisScore, McpServerRegistry

router = APIRouter(prefix="/api", tags=["scoring_summary_api"])


class SummaryDay(BaseModel):
    date: str = Field(..., description="Date (YYYY-MM-DD)")
    total_rows: int = Field(..., description="Number of axis score rows that day")
    unique_servers: int = Field(..., description="Distinct servers scored that day")
    avg_p_top: float = Field(..., description="Mean p_top across rows")
    tier_counts: Dict[str, int] = Field(..., description="Row count per risk_tier")


class SummaryResponse(BaseModel):
    summary: List[SummaryDay]


@router.get("/scoring/summary", response_model=SummaryResponse)
def get_scoring_summary(
    days: int = Field(default=30, ge=1, le=365, description="Look-back window in days"),
    db: Session = Depends(get_session),
) -> SummaryResponse:
    """
    Per-day scoring summary: rows, unique servers, avg p_top, and risk-tier
    breakdown for the last *days* days.
    """
    cutoff = datetime.utcnow().date() - __import__("datetime").timedelta(days=days)

    rows = (
        db.query(
            func.date(McpLlmAxisScore.scored_at).label("day"),
            McpLlmAxisScore.p_top,
            McpServerRegistry.risk_tier,
            McpLlmAxisScore.server_id,
        )
        .join(
            McpServerRegistry,
            McpLlmAxisScore.server_id == McpServerRegistry.server_id,
        )
        .filter(func.date(McpLlmAxisScore.scored_at) >= cutoff)
        .all()
    )

    from collections import defaultdict

    grouped: Dict[date, List] = defaultdict(list)
    for day, p_top, risk_tier, server_id in rows:
        if day is not None:
            grouped[day].append((p_top, risk_tier, server_id))

    summary_list: List[SummaryDay] = []
    for day, items in sorted(grouped.items()):
        total_rows = len(items)
        unique_servers = len({srv for _, _, srv in items})
        valid_p = [p for p, _, _ in items if p is not None]
        avg_p_top = sum(valid_p) / len(valid_p) if valid_p else 0.0
        tier_counts: Dict[str, int] = defaultdict(int)
        for _, tier, _ in items:
            tier_counts[tier or "unknown"] += 1
        summary_list.append(
            SummaryDay(
                date=day.isoformat(),
                total_rows=total_rows,
                unique_servers=unique_servers,
                avg_p_top=round(avg_p_top, 4),
                tier_counts=dict(tier_counts),
            )
        )

    return SummaryResponse(summary=summary_list)


# --------------------------------------------------------------------------- #
# Self-test
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    Base.metadata.create_all(engine)

    def get_test_session() -> Session:
        return SessionLocal()

    # Seed data
    with SessionLocal() as db:
        srv1 = McpServerRegistry(
            server_id="srv-1",
            name="Server One",
            risk_tier="high",
            confidence=0.9,
            description="",
            registry_source="test",
            trust_score=0.5,
            url="",
            verdict="",
            verdict_reasoning="",
            first_seen=datetime.utcnow(),
            last_assessed=datetime.utcnow(),
            last_scanned=datetime.utcnow(),
            last_seen=datetime.utcnow(),
            meta={},
            scan_count=1,
        )
        srv2 = McpServerRegistry(
            server_id="srv-2",
            name="Server Two",
            risk_tier="medium",
            confidence=0.8,
            description="",
            registry_source="test",
            trust_score=0.6,
            url="",
            verdict="",
            verdict_reasoning="",
            first_seen=datetime.utcnow(),
            last_assessed=datetime.utcnow(),
            last_scanned=datetime.utcnow(),
            last_seen=datetime.utcnow(),
            meta={},
            scan_count=1,
        )
        db.add_all([srv1, srv2])
        db.flush()

        # Day 1
        db.add(
            McpLlmAxisScore(
                server_id="srv-1",
                adapter_sha256="a" * 64,
                axis_name="overall_risk",
                label="HIGH",
                label_index=2,
                model_version="m1",
                decision_rule_version="v1",
                probs={"HIGH": 0.8, "MEDIUM": 0.15, "LOW": 0.05},
                p_top=0.8,
                p_critical=0.1,
                p_danger=0.2,
                escalated=False,
                scored_at=datetime(2025, 1, 1, 10, 0, 0),
            )
        )
        db.add(
            McpLlmAxisScore(
                server_id="srv-2",
                adapter_sha256="b" * 64,
                axis_name="overall_risk",
                label="MEDIUM",
                label_index=1,
                model_version="m1",
                decision_rule_version="v1",
                probs={"HIGH": 0.3, "MEDIUM": 0.6, "LOW": 0.1},
                p_top=0.6,
                p_critical=0.05,
                p_danger=0.15,
                escalated=False,
                scored_at=datetime(2025, 1, 1, 11, 0, 0),
            )
        )
        # Day 2
        db.add(
            McpLlmAxisScore(
                server_id="srv-1",
                adapter_sha256="c" * 64,
                axis_name="overall_risk",
                label="HIGH",
                label_index=2,
                model_version="m1",
                decision_rule_version="v1",
                probs={"HIGH": 0.75, "MEDIUM": 0.2, "LOW": 0.05},
                p_top=0.75,
                p_critical=0.12,
                p_danger=0.22,
                escalated=False,
                scored_at=datetime(2025, 1, 2, 10, 0, 0),
            )
        )
        db.commit()

    # Wire up test app
    app = FastAPI()
    app.include_router(router)

    # Correct spelling: FastAPI instance from app.main
    from app.main import app as main_app

    main_app.dependency_overrides[get_session] = get_test_session

    client = TestClient(main_app)

    resp = client.get("/api/scoring/summary")
    assert resp.status_code == 200, f"Unexpected status {resp.status_code}: {resp.text}"
    data = resp.json()
    assert "summary" in data, "Missing 'summary' key"
    summary = data["summary"]
    assert isinstance(summary, list), "'summary' must be a list"

    dates = {item["date"] for item in summary}
    assert "2025-01-01" in dates, f"Missing 2025-01-01 in {dates}"
    assert "2025-01-02" in dates, f"Missing 2025-01-02 in {dates}"

    for item in summary:
        assert isinstance(item["avg_p_top"], float), "avg_p_top must be float"
        assert isinstance(item["tier_counts"], dict), "tier_counts must be dict"
        assert isinstance(item["total_rows"], int), "total_rows must be int"
        assert isinstance(item["unique_servers"], int), "unique_servers must be int"

    print("PASS")
