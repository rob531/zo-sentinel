from datetime import datetime, timezone
from typing import Dict, List, Optional

from fastapi import APIRouter, Depends
from fastapi.testclient import TestClient
from pydantic import BaseModel, Field
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry

router = APIRouter(prefix="/api", tags=["verdict-confidence"])


class LowestConfidenceServer(BaseModel):
    server_id: str
    name: Optional[str] = None
    verdict: Optional[str] = None
    risk_tier: Optional[str] = None
    confidence: Optional[float] = None
    last_assessed: Optional[datetime] = None


class VerdictConfidenceResponse(BaseModel):
    generated_at: datetime
    total_servers: int
    missing_confidence: int
    bands: Dict[str, int]
    by_verdict: Dict[str, int]
    by_risk_tier: Dict[str, int]
    tier_disagreements: int
    lowest_confidence: List[LowestConfidenceServer]


def _axis_name_column():
    columns = McpLlmAxisScore.__table__.columns
    for name in (
        "axis_name",
        "axis",
        "axis_key",
        "axis_type",
        "dimension",
        "metric_name",
    ):
        if name in columns:
            return columns[name]
    raise RuntimeError("McpLlmAxisScore has no recognized axis-name column")


def _implied_risk_tier(p_top: float) -> str:
    if p_top >= 0.8:
        return "high"
    if p_top >= 0.5:
        return "medium"
    return "low"


def _normalized_tier(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    normalized = value.strip().lower().replace("-", "_").replace(" ", "_")
    aliases = {
        "moderate": "medium",
        "med": "medium",
        "hi": "high",
        "lo": "low",
    }
    return aliases.get(normalized, normalized)


def get_verdict_confidence(
    session: Session = Depends(get_session),
) -> VerdictConfidenceResponse:
    registry_rows = session.execute(
        select(
            McpServerRegistry.server_id,
            McpServerRegistry.name,
            McpServerRegistry.registry_source,
            McpServerRegistry.verdict,
            McpServerRegistry.confidence,
            McpServerRegistry.risk_tier,
            McpServerRegistry.last_assessed,
            McpServerRegistry.last_seen,
        )
    ).mappings().all()

    bands = {
        "lt_0.4": 0,
        "0.4_to_0.6": 0,
        "0.6_to_0.8": 0,
        "gte_0.8": 0,
        "missing": 0,
    }
    by_verdict: Dict[str, int] = {}
    by_risk_tier: Dict[str, int] = {}
    missing_confidence = 0

    for row in registry_rows:
        confidence = row["confidence"]
        if confidence is None:
            bands["missing"] += 1
            missing_confidence += 1
        elif confidence < 0.4:
            bands["lt_0.4"] += 1
        elif confidence < 0.6:
            bands["0.4_to_0.6"] += 1
        elif confidence < 0.8:
            bands["0.6_to_0.8"] += 1
        else:
            bands["gte_0.8"] += 1

        verdict_key = row["verdict"] if row["verdict"] is not None else "missing"
        tier_key = row["risk_tier"] if row["risk_tier"] is not None else "missing"
        by_verdict[verdict_key] = by_verdict.get(verdict_key, 0) + 1
        by_risk_tier[tier_key] = by_risk_tier.get(tier_key, 0) + 1

    axis_name = _axis_name_column()
    axis_table = McpLlmAxisScore.__table__
    axis_rows = session.execute(
        select(axis_table.c["server_id"], axis_table.c["p_top"]).where(
            axis_name == "overall_risk"
        )
    ).mappings().all()
    axis_scores = {
        row["server_id"]: float(row["p_top"])
        for row in axis_rows
        if row["server_id"] is not None and row["p_top"] is not None
    }

    tier_disagreements = 0
    for row in registry_rows:
        p_top = axis_scores.get(row["server_id"])
        registry_tier = _normalized_tier(row["risk_tier"])
        if p_top is None or registry_tier is None:
            continue
        if registry_tier != _implied_risk_tier(p_top):
            tier_disagreements += 1

    lowest_rows = sorted(
        (row for row in registry_rows if row["confidence"] is not None),
        key=lambda row: float(row["confidence"]),
    )[:25]

    return VerdictConfidenceResponse(
        generated_at=datetime.now(timezone.utc),
        total_servers=len(registry_rows),
        missing_confidence=missing_confidence,
        bands=bands,
        by_verdict=by_verdict,
        by_risk_tier=by_risk_tier,
        tier_disagreements=tier_disagreements,
        lowest_confidence=[
            LowestConfidenceServer(
                server_id=row["server_id"],
                name=row["name"],
                verdict=row["verdict"],
                risk_tier=row["risk_tier"],
                confidence=float(row["confidence"]),
                last_assessed=row["last_assessed"],
            )
            for row in lowest_rows
        ],
    )


router.add_api_route(
    "/verdict/confidence",
    get_verdict_confidence,
    methods=["GET"],
    response_model=VerdictConfidenceResponse,
)


if __name__ == "__main__":
    from fastapi import FastAPI
    from sqlalchemy.orm import sessionmaker

    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    McpServerRegistry.metadata.create_all(
        test_engine,
        tables=[McpServerRegistry.__table__, McpLlmAxisScore.__table__],
    )
    TestSession = sessionmaker(bind=test_engine, expire_on_commit=False)

    with TestSession() as session:
        session.add_all(
            [
                McpServerRegistry(
                    server_id="contract-low",
                    name="Low Confidence Server",
                    registry_source="acceptance",
                    verdict="review",
                    confidence=0.2,
                    risk_tier="low",
                    last_assessed=datetime.now(timezone.utc),
                    last_seen=datetime.now(timezone.utc),
                ),
                McpServerRegistry(
                    server_id="contract-high",
                    name="High Confidence Server",
                    registry_source="acceptance",
                    verdict="trusted",
                    confidence=0.95,
                    risk_tier="high",
                    last_assessed=datetime.now(timezone.utc),
                    last_seen=datetime.now(timezone.utc),
                ),
                McpServerRegistry(
                    server_id="contract-missing",
                    name="Missing Confidence Server",
                    registry_source="acceptance",
                    verdict="unknown",
                    confidence=None,
                    risk_tier="medium",
                    last_assessed=datetime.now(timezone.utc),
                    last_seen=datetime.now(timezone.utc),
                ),
            ]
        )
        session.commit()

    test_app = FastAPI()
    test_app.include_router(router)

    def override_get_session():
        with TestSession() as session:
            yield session

    test_app.dependency_overrides[get_session] = override_get_session

    response = TestClient(test_app).get("/api/verdict/confidence")
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["total_servers"] == 3
    assert payload["missing_confidence"] == 1
    assert sum(payload["bands"].values()) == 3
    assert payload["bands"]["lt_0.4"] == 1
    assert payload["bands"]["gte_0.8"] == 1
    assert payload["bands"]["missing"] == 1
    print("PASS")