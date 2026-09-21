# services/staged/server_risk_tier/contract.py
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.orm import Session
from sqlalchemy import text

# Real data layer imports (must remain unchanged for production)
from app.db import get_session  # pragma: no cover

router = APIRouter(prefix="/api")


class RiskTierResponse(BaseModel):
    server_id: int
    composite_score: float
    risk_tier: str
    verdict: str
    axis_count: int
    criteria_version: str | None = None


def _determine_risk_tier(composite: float, axis_cnt: int) -> str:
    """Map composite score & axis count to a risk tier."""
    if axis_cnt < 5:
        return "INSUFFICIENT"
    if composite > 75:
        return "TRUSTED_GENERAL"
    if composite > 60:
        return "TRUSTED_RESEARCH"
    if composite > 45:
        return "ENTERPRISE_CONTROLLED"
    if composite > 30:
        return "CAUTION_LIMITED"
    if composite > 15:
        return "HIGH_RISK_ISOLATED"
    return "KNOWN_THREAT"


@router.get(
    "/servers/{server_id}/risk-tier",
    response_model=RiskTierResponse,
    status_code=status.HTTP_200_OK,
)
def get_server_risk_tier(
    server_id: int,
    db: Session = Depends(get_session),
):
    """Return risk‑tier information for a given server."""
    # fetch axis scores for the server
    axis_rows = db.execute(
        text(
            """
            SELECT score, weight
            FROM McpLlmAxisScore
            WHERE server_id = :sid
            """
        ),
        {"sid": server_id},
    ).fetchall()

    if not axis_rows:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Server not found or no axis scores available",
        )

    # compute weighted composite score
    total_weight = sum(row["weight"] for row in axis_rows) or 1.0
    composite = sum(row["score"] * row["weight"] for row in axis_rows) / total_weight

    # fetch registry info (criteria_version)
    reg_row = db.execute(
        text(
            """
            SELECT criteria_version
            FROM McpServerRegistry
            WHERE server_id = :sid
            """
        ),
        {"sid": server_id},
    ).fetchone()
    criteria_version = reg_row["criteria_version"] if reg_row else None

    risk_tier = _determine_risk_tier(composite, len(axis_rows))
    verdict = "OK"  # placeholder – real implementation may differ

    return RiskTierResponse(
        server_id=server_id,
        composite_score=composite,
        risk_tier=risk_tier,
        verdict=verdict,
        axis_count=len(axis_rows),
        criteria_version=criteria_version,
    )


# --------------------------------------------------------------------------- #
# Self‑test (run with: python -m services.staged.server_risk_tier.contract)
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import (
        create_engine,
        MetaData,
        Table,
        Column,
        Integer,
        Float,
        String,
    )
    from sqlalchemy.orm import sessionmaker

    # ------------------------------------------------------------------- #
    # In‑memory SQLite setup (used only for the self‑test)
    # ------------------------------------------------------------------- #
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
    )
    metadata = MetaData()

    server_registry = Table(
        "McpServerRegistry",
        metadata,
        Column("server_id", Integer, primary_key=True),
        Column("criteria_version", String, nullable=True),
    )

    llm_axis_scores = Table(
        "McpLlmAxisScore",
        metadata,
        Column("id", Integer, primary_key=True, autoincrement=True),
        Column("server_id", Integer, nullable=False),
        Column("score", Float, nullable=False),
        Column("weight", Float, nullable=False),
    )

    metadata.create_all(engine)

    SessionLocal = sessionmaker(bind=engine)

    def _test_session() -> Session:
        return SessionLocal()

    # ------------------------------------------------------------------- #
    # Seed test data: three servers covering distinct tiers
    # ------------------------------------------------------------------- #
    with engine.begin() as conn:
        # Server 1 – high scores → TRUSTED_GENERAL
        conn.execute(
            server_registry.insert().values(server_id=1, criteria_version="v1")
        )
        for _ in range(6):
            conn.execute(
                llm_axis_scores.insert().values(
                    server_id=1, score=80.0, weight=1.0
                )
            )

        # Server 2 – moderate scores → ENTERPRISE_CONTROLLED
        conn.execute(
            server_registry.insert().values(server_id=2, criteria_version="v1")
        )
        for _ in range(6):
            conn.execute(
                llm_axis_scores.insert().values(
                    server_id=2, score=50.0, weight=1.0
                )
            )

        # Server 3 – insufficient axes (<5) → INSUFFICIENT
        conn.execute(
            server_registry.insert().values(server_id=3, criteria_version="v1")
        )
        for _ in range(3):
            conn.execute(
                llm_axis_scores.insert().values(
                    server_id=3, score=30.0, weight=1.0
                )
            )

    # ------------------------------------------------------------------- #
    # FastAPI app wiring with dependency override
    # ------------------------------------------------------------------- #
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = _test_session

    client = TestClient(app)

    # ------------------------------------------------------------------- #
    # Execute acceptance checks
    # ------------------------------------------------------------------- #
    expected = {
        1: "TRUSTED_GENERAL",
        2: "ENTERPRISE_CONTROLLED",
        3: "INSUFFICIENT",
    }

    for sid, tier in expected.items():
        resp = client.get(f"/api/servers/{sid}/risk-tier")
        assert resp.status_code == 200, f"Server {sid} returned {resp.status_code}"
        payload = resp.json()
        assert isinstance(payload["composite_score"], float), "composite_score not float"
        assert payload["risk_tier"] == tier, f"Server {sid} tier mismatch"
        assert payload["risk_tier"] in [
            "TRUSTED_GENERAL",
            "TRUSTED_RESEARCH",
            "ENTERPRISE_CONTROLLED",
            "CAUTION_LIMITED",
            "HIGH_RISK_ISOLATED",
            "KNOWN_THREAT",
            "INSUFFICIENT",
        ]

    print("PASS")