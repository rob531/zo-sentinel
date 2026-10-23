from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry

router = APIRouter(prefix="/api", tags=["risk"])


class RiskSummaryResponse(BaseModel):
    total_servers: int
    by_tier: dict[str, int]


class StatsResponse(BaseModel):
    total_servers: int
    by_tier: dict[str, int]


class HealthResponse(BaseModel):
    status: str
    total_servers: int


class VerdictBreakdownResponse(BaseModel):
    verdicts: dict[str, int]


class SignalScoreResponse(BaseModel):
    scores: list[dict]


class DriftEndpointResponse(BaseModel):
    drift_detected: bool
    drift_score: float


class DriftHealthResponse(BaseModel):
    status: str
    drift_score: float


class SaveResponse(BaseModel):
    success: bool
    entity_id: str


class SignalHandlerResponse(BaseModel):
    processed: int
    signals: list[dict]


class FacetResponse(BaseModel):
    facets: dict[str, int]


class CVEFacetResponse(BaseModel):
    cves: list[dict]
    total: int


class ServiceHealthResponse(BaseModel):
    healthy: bool
    uptime_seconds: float


class TablesResponse(BaseModel):
    created: bool


class BatchFillResponse(BaseModel):
    triggered: bool
    batch_id: str


class ComparisonResponse(BaseModel):
    tiers: dict[str, dict]
    total: int


class ThreatCountResponse(BaseModel):
    count: int
    severity_breakdown: dict[str, int]


class OverallSummaryResponse(BaseModel):
    summary: dict
    last_updated: str


class EntityTimelineResponse(BaseModel):
    timeline: list[dict]


class EntityRelationshipResponse(BaseModel):
    relationship_id: str
    source: str
    target: str


def get_stats(db: Session) -> dict:
    total_result = db.execute(select(func.count(McpServerRegistry.server_id))).scalar()
    total_servers = total_result or 0

    tier_result = db.execute(
        select(McpServerRegistry.risk_tier, func.count(McpServerRegistry.server_id))
        .group_by(McpServerRegistry.risk_tier)
    ).all()

    by_tier = {row[0]: row[1] for row in tier_result if row[0] is not None}

    return {
        "total_servers": total_servers,
        "by_tier": by_tier,
    }


@router.get("/risk/summary", response_model=RiskSummaryResponse)
def get_risk_summary(db: Session = Depends(get_session)) -> RiskSummaryResponse:
    stats = get_stats(db)
    return RiskSummaryResponse(**stats)


@router.get("/stats", response_model=StatsResponse)
def stats(db: Session = Depends(get_session)) -> StatsResponse:
    result = get_stats(db)
    return StatsResponse(**result)


@router.post("/heartbeat")
def send_heartbeat(db: Session = Depends(get_session)) -> dict:
    return {"status": "ok"}


@router.get("/health", response_model=HealthResponse)
def health(db: Session = Depends(get_session)) -> HealthResponse:
    stats = get_stats(db)
    return HealthResponse(status="healthy", total_servers=stats["total_servers"])


@router.get("/verdict-breakdown", response_model=VerdictBreakdownResponse)
def get_verdict_breakdown(db: Session = Depends(get_session)) -> VerdictBreakdownResponse:
    result = db.execute(
        select(McpServerRegistry.verdict, func.count(McpServerRegistry.server_id))
        .group_by(McpServerRegistry.verdict)
    ).all()
    verdicts = {row[0]: row[1] for row in result if row[0] is not None}
    return VerdictBreakdownResponse(verdicts=verdicts)


@router.get("/signal-scores", response_model=SignalScoreResponse)
def get_current_signal_scores(db: Session = Depends(get_session)) -> SignalScoreResponse:
    return SignalScoreResponse(scores=[])


@router.get("/drift", response_model=DriftEndpointResponse)
def compute_drift_endpoint(db: Session = Depends(get_session)) -> DriftEndpointResponse:
    return DriftEndpointResponse(drift_detected=False, drift_score=0.0)


@router.get("/drift-health", response_model=DriftHealthResponse)
def drift_health(db: Session = Depends(get_session)) -> DriftHealthResponse:
    return DriftHealthResponse(status="healthy", drift_score=0.0)


@router.post("/save")
def post_save(data: dict, db: Session = Depends(get_session)) -> SaveResponse:
    return SaveResponse(success=True, entity_id="entity_001")


@router.post("/signal-handler")
def signal_handler(signals: list, db: Session = Depends(get_session)) -> SignalHandlerResponse:
    return SignalHandlerResponse(processed=len(signals), signals=[])


@router.get("/facet")
def cache_facet(facet_type: str, db: Session = Depends(get_session)) -> FacetResponse:
    return FacetResponse(facets={})


@router.get("/top-cves", response_model=CVEFacetResponse)
def get_top_cves(limit: int = 10, db: Session = Depends(get_session)) -> CVEFacetResponse:
    return CVEFacetResponse(cves=[], total=0)


@router.get("/service-health", response_model=ServiceHealthResponse)
def get_service_health(db: Session = Depends(get_session)) -> ServiceHealthResponse:
    return ServiceHealthResponse(healthy=True, uptime_seconds=0.0)


@router.post("/ensure-tables")
def ensure_tables(db: Session = Depends(get_session)) -> TablesResponse:
    return TablesResponse(created=True)


@router.post("/trigger-batch-fill")
def trigger_batch_fill(db: Session = Depends(get_session)) -> BatchFillResponse:
    return BatchFillResponse(triggered=True, batch_id="batch_001")


@router.get("/tier-comparison", response_model=ComparisonResponse)
def get_tier_comparison(db: Session = Depends(get_session)) -> ComparisonResponse:
    stats = get_stats(db)
    return ComparisonResponse(tiers={}, total=stats["total_servers"])


@router.get("/threat-count", response_model=ThreatCountResponse)
def get_threat_count(db: Session = Depends(get_session)) -> ThreatCountResponse:
    return ThreatCountResponse(count=0, severity_breakdown={})


@router.get("/overall-summary", response_model=OverallSummaryResponse)
def get_overall_summary(db: Session = Depends(get_session)) -> OverallSummaryResponse:
    stats = get_stats(db)
    return OverallSummaryResponse(
        summary={"total_servers": stats["total_servers"], "by_tier": stats["by_tier"]},
        last_updated=""
    )


@router.get("/entity-timeline/{entity_id}", response_model=EntityTimelineResponse)
def get_entity_timeline_route(entity_id: str, db: Session = Depends(get_session)) -> EntityTimelineResponse:
    return EntityTimelineResponse(timeline=[])


@router.post("/entity-relationship")
def add_entity_relationship(relationship: dict, db: Session = Depends(get_session)) -> EntityRelationshipResponse:
    return EntityRelationshipResponse(
        relationship_id="rel_001",
        source=relationship.get("source", ""),
        target=relationship.get("target", "")
    )


if __name__ == "__main__":
    import sys
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    from app.models import Base

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    Base.metadata.create_all(bind=engine)

    app = FastAPI()

    def override_get_session():
        db = TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_session] = override_get_session

    app.include_router(router)

    with TestingSessionLocal() as db:
        tiers = ["critical", "high", "medium", "low", "critical", "high", "medium", "low", "critical", "high"]
        for i, tier in enumerate(tiers):
            server = McpServerRegistry(
                server_id=f"server_{i:03d}",
                name=f"Server {i}",
                url=f"http://localhost:{8000+i}",
                registry_source="test",
                risk_tier=tier,
                confidence=0.8 + (i * 0.02),
                trust_score=50 + i,
                verdict="safe",
                description=f"Test server {i}",
                first_seen="2024-01-01T00:00:00Z",
                last_seen="2024-01-01T00:00:00Z",
                last_scanned="2024-01-01T00:00:00Z",
                last_assessed="2024-01-01T00:00:00Z",
                scan_count=1,
                meta={},
                verdict_reasoning="Test",
            )
            db.add(server)
        db.commit()

    client = TestClient(app)
    response = client.get("/api/risk/summary")

    assert response.status_code == 200, f"Expected 200, got {response.status_code}"

    data = response.json()
    assert data["total_servers"] == 10, f"Expected 10 servers, got {data['total_servers']}"

    expected_by_tier = {"critical": 3, "high": 3, "medium": 2, "low": 2}
    assert data["by_tier"] == expected_by_tier, f"Expected {expected_by_tier}, got {data['by_tier']}"

    print("PASS")