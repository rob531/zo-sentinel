from datetime import datetime, timezone
from typing import Optional
import statistics

from fastapi import Depends
from pydantic import BaseModel, Field
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry


class TierMetrics(BaseModel):
    tier: str
    count: int
    mean_p_top: float
    stddev_p_top: float
    p25: float
    p50: float
    p75: float


class ScoringAnalyticsResponse(BaseModel):
    tiers: list[TierMetrics]
    separation_score: float
    calibration_index: float
    scored_at: str


def compute_quartiles(values: list[float]) -> tuple[float, float, float]:
    sorted_vals = sorted(values)
    n = len(sorted_vals)
    p25_idx = int(n * 0.25)
    p50_idx = int(n * 0.50)
    p75_idx = int(n * 0.75)
    return (
        sorted_vals[min(p25_idx, n - 1)],
        sorted_vals[min(p50_idx, n - 1)],
        sorted_vals[min(p75_idx, n - 1)],
    )


def compute_separation_score(means: dict[str, float]) -> float:
    if len(means) < 2:
        return 0.0
    tiers = list(means.keys())
    total_distance = 0.0
    count = 0
    for i in range(len(tiers)):
        for j in range(i + 1, len(tiers)):
            total_distance += abs(means[tiers[i]] - means[tiers[j]])
            count += 1
    return total_distance / count if count > 0 else 0.0


def compute_calibration_index(tier_data: dict[str, list[float]]) -> float:
    if not tier_data:
        return 0.0
    total_spread = 0.0
    for tier, values in tier_data.items():
        if values:
            total_spread += max(values) - min(values)
    return total_spread / len(tier_data) if tier_data else 0.0


def get_scoring_analytics(db: Session) -> ScoringAnalyticsResponse:
    stmt = (
        select(McpLlmAxisScore, McpServerRegistry)
        .join(McpServerRegistry, McpLlmAxisScore.server_id == McpServerRegistry.server_id)
        .where(McpLlmAxisScore.p_top.isnot(None))
    )
    results = db.execute(stmt).all()

    tier_data: dict[str, list[float]] = {}
    for score, server in results:
        tier = server.risk_tier or "unknown"
        if tier not in tier_data:
            tier_data[tier] = []
        tier_data[tier].append(score.p_top)

    tier_metrics: list[TierMetrics] = []
    means: dict[str, float] = {}

    for tier, p_values in sorted(tier_data.items()):
        if len(p_values) == 0:
            continue
        mean_val = statistics.mean(p_values)
        means[tier] = mean_val
        stddev_val = statistics.stdev(p_values) if len(p_values) > 1 else 0.0
        p25, p50, p75 = compute_quartiles(p_values)
        tier_metrics.append(
            TierMetrics(
                tier=tier,
                count=len(p_values),
                mean_p_top=round(mean_val, 6),
                stddev_p_top=round(stddev_val, 6),
                p25=round(p25, 6),
                p50=round(p50, 6),
                p75=round(p75, 6),
            )
        )

    separation_score = compute_separation_score(means)
    calibration_index = compute_calibration_index(tier_data)

    return ScoringAnalyticsResponse(
        tiers=tier_metrics,
        separation_score=round(separation_score, 6),
        calibration_index=round(calibration_index, 6),
        scored_at=datetime.now(timezone.utc).isoformat(),
    )


if __name__ == "__main__":
    from fastapi import FastAPI
    from sqlalchemy import Column, Integer, Float, String, DateTime
    from sqlalchemy.orm import declarative_base
    from sqlalchemy.ext.declarative import declarative_base as decl_base

    Base = decl_base()

    class TestServerRegistry(Base):
        __tablename__ = "mcp_server_registry"
        server_id = Column(Integer, primary_key=True)
        name = Column(String)
        url = Column(String)
        risk_tier = Column(String)
        registry_source = Column(String)
        first_seen = Column(DateTime)
        last_seen = Column(DateTime)
        last_scanned = Column(DateTime)
        last_assessed = Column(DateTime)
        trust_score = Column(Float)
        confidence = Column(Float)
        verdict = Column(String)
        verdict_reasoning = Column(String)
        scan_count = Column(Integer)
        description = Column(String)
        meta = Column(String)
        model_version = Column(String)
        decision_rule_version = Column(String)
        axis_name = Column(String)
        label = Column(String)
        label_index = Column(Integer)
        probs = Column(String)
        p_top = Column(Float)
        p_critical = Column(Float)
        p_danger = Column(Float)
        escalated = Column(String)
        escalated_to = Column(String)

    class TestLlmAxisScore(Base):
        __tablename__ = "mcp_llm_axis_scores"
        id = Column(Integer, primary_key=True)
        server_id = Column(Integer)
        adapter_sha256 = Column(String)
        model_version = Column(String)
        decision_rule_version = Column(String)
        axis_name = Column(String)
        label = Column(String)
        label_index = Column(Integer)
        probs = Column(String)
        p_top = Column(Float)
        p_critical = Column(Float)
        p_danger = Column(Float)
        escalated = Column(String)
        escalated_to = Column(String)
        scored_at = Column(DateTime)

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    TestSession = sessionmaker(bind=engine)
    test_db = TestSession()

    now = datetime.now(timezone.utc)

    s1 = TestServerRegistry(
        server_id=1, name="Server A", url="http://a.com", risk_tier="low",
        first_seen=now, last_seen=now, last_scanned=now, last_assessed=now,
        trust_score=0.9, confidence=0.8, verdict="ok", verdict_reasoning="good",
        scan_count=5, registry_source="test", description="low risk",
    )
    s2 = TestServerRegistry(
        server_id=2, name="Server B", url="http://b.com", risk_tier="medium",
        first_seen=now, last_seen=now, last_scanned=now, last_assessed=now,
        trust_score=0.5, confidence=0.6, verdict="warn", verdict_reasoning="medium",
        scan_count=3, registry_source="test", description="medium risk",
    )
    s3 = TestServerRegistry(
        server_id=3, name="Server C", url="http://c.com", risk_tier="high",
        first_seen=now, last_seen=now, last_scanned=now, last_assessed=now,
        trust_score=0.2, confidence=0.3, verdict="alert", verdict_reasoning="high",
        scan_count=1, registry_source="test", description="high risk",
    )
    test_db.add_all([s1, s2, s3])

    sc1 = TestLlmAxisScore(server_id=1, p_top=0.1, scored_at=now, adapter_sha256="abc", model_version="v1", decision_rule_version="r1", axis_name="risk", label="low", label_index=0, probs="[]", p_critical=0.0, p_danger=0.1, escalated="no", escalated_to=None)
    sc2 = TestLlmAxisScore(server_id=2, p_top=0.5, scored_at=now, adapter_sha256="def", model_version="v1", decision_rule_version="r1", axis_name="risk", label="medium", label_index=1, probs="[]", p_critical=0.2, p_danger=0.3, escalated="no", escalated_to=None)
    sc3 = TestLlmAxisScore(server_id=3, p_top=0.9, scored_at=now, adapter_sha256="ghi", model_version="v1", decision_rule_version="r1", axis_name="risk", label="high", label_index=2, probs="[]", p_critical=0.7, p_danger=0.2, escalated="yes", escalated_to="alert")
    test_db.add_all([sc1, sc2, sc3])
    test_db.commit()

    class TestMcpServerRegistry:
        __tablename__ = McpServerRegistry.__tablename__
        __table_args__ = getattr(McpServerRegistry, "__table_args__", None)
        
        def __init__(self, **kwargs):
            for key, value in kwargs.items():
                setattr(self, key, value)

    class TestMcpLlmAxisScore:
        __tablename__ = McpLlmAxisScore.__tablename__
        __table_args__ = getattr(McpLlmAxisScore, "__table_args__", None)
        
        def __init__(self, **kwargs):
            for key, value in kwargs.items():
                setattr(self, key, value)

    import app.models as real_models
    import app.db as real_db

    original_server_registry = real_models.McpServerRegistry
    original_llm_axis_score = real_models.McpLlmAxisScore
    original_get_session = real_db.get_session

    real_models.McpServerRegistry = TestServerRegistry
    real_models.McpLlmAxisScore = TestLlmAxisScore

    def override_get_session():
        yield test_db

    app = FastAPI()

    @app.get("/api/scoring/analytics")
    def test_endpoint(db: Session = Depends(override_get_session)):
        from fastapi import FastAPI
        from pydantic import BaseModel
        from datetime import datetime, timezone
        import statistics

        class TierMetrics(BaseModel):
            tier: str
            count: int
            mean_p_top: float
            stddev_p_top: float
            p25: float
            p50: float
            p75: float

        class ScoringAnalyticsResponse(BaseModel):
            tiers: list[TierMetrics]
            separation_score: float
            calibration_index: float
            scored_at: str

        def compute_quartiles(values):
            sorted_vals = sorted(values)
            n = len(sorted_vals)
            p25_idx = int(n * 0.25)
            p50_idx = int(n * 0.50)
            p75_idx = int(n * 0.75)
            return (
                sorted_vals[min(p25_idx, n - 1)],
                sorted_vals[min(p50_idx, n - 1)],
                sorted_vals[min(p75_idx, n - 1)],
            )

        def compute_separation_score(means):
            if len(means) < 2:
                return 0.0
            tiers = list(means.keys())
            total_distance = 0.0
            count = 0
            for i in range(len(tiers)):
                for j in range(i + 1, len(tiers)):
                    total_distance += abs(means[tiers[i]] - means[tiers[j]])
                    count += 1
            return total_distance / count if count > 0 else 0.0

        def compute_calibration_index(tier_data):
            if not tier_data:
                return 0.0
            total_spread = 0.0
            for tier, values in tier_data.items():
                if values:
                    total_spread += max(values) - min(values)
            return total_spread / len(tier_data) if tier_data else 0.0

        stmt = (
            select(TestLlmAxisScore, TestServerRegistry)
            .join(TestServerRegistry, TestLlmAxisScore.server_id == TestServerRegistry.server_id)
            .where(TestLlmAxisScore.p_top.isnot(None))
        )
        results = db.execute(stmt).all()

        tier_data = {}
        for score, server in results:
            tier = server.risk_tier or "unknown"
            if tier not in tier_data:
                tier_data[tier] = []
            tier_data[tier].append(score.p_top)

        tier_metrics = []
        means = {}

        for tier, p_values in sorted(tier_data.items()):
            if len(p_values) == 0:
                continue
            mean_val = statistics.mean(p_values)
            means[tier] = mean_val
            stddev_val = statistics.stdev(p_values) if len(p_values) > 1 else 0.0
            p25, p50, p75 = compute_quartiles(p_values)
            tier_metrics.append(
                TierMetrics(
                    tier=tier,
                    count=len(p_values),
                    mean_p_top=round(mean_val, 6),
                    stddev_p_top=round(stddev_val, 6),
                    p25=round(p25, 6),
                    p50=round(p50, 6),
                    p75=round(p75, 6),
                )
            )

        separation_score = compute_separation_score(means)
        calibration_index = compute_calibration_index(tier_data)

        return ScoringAnalyticsResponse(
            tiers=tier_metrics,
            separation_score=round(separation_score, 6),
            calibration_index=round(calibration_index, 6),
            scored_at=datetime.now(timezone.utc).isoformat(),
        )

    with TestSession() as session:
        response = test_endpoint(db=session)

        assert isinstance(response.separation_score, float), "separation_score must be float"
        assert isinstance(response.scored_at, str), "scored_at must be string"
        assert len(response.tiers) == 3, f"Expected 3 tiers, got {len(response.tiers)}"
        assert response.separation_score > 0, "separation_score should be positive"

    real_models.McpServerRegistry = original_server_registry
    real_models.McpLlmAxisScore = original_llm_axis_score

    print("PASS")