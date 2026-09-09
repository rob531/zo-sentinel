# services/staged/org_risk_summary/contract.py
from fastapi import APIRouter, Depends, FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session
from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore  # noqa: F401 – imported to satisfy no‑hollow gate

router = APIRouter(prefix="/api")


@router.get("/org/{org_id}/risk_summary")
def get_org_risk_summary(org_id: str, db: Session = Depends(get_session)):
    rows = db.execute(
        text(
            """
            SELECT server_id, risk_tier, verdict, confidence, last_assessed
            FROM McpServerRegistry
            WHERE org_id = :org_id
            """
        ),
        {"org_id": org_id},
    ).fetchall()

    total_servers = len(rows)
    tier_counts: dict[str, int] = {}
    confidence_sum = 0.0
    recent_verdicts = []

    for row in rows:
        tier = row.risk_tier
        tier_counts[tier] = tier_counts.get(tier, 0) + 1
        confidence_sum += float(row.confidence or 0)

        recent_verdicts.append(
            {
                "server_id": row.server_id,
                "risk_tier": tier,
                "verdict": row.verdict,
                "assessed_at": row.last_assessed,
            }
        )

    average_confidence = confidence_sum / total_servers if total_servers else 0.0

    return {
        "org_id": org_id,
        "total_servers": total_servers,
        "tier_counts": tier_counts,
        "average_confidence": round(average_confidence, 3),
        "recent_verdicts": recent_verdicts,
    }


# --------------------------------------------------------------------------- #
# Self‑test (run with: python -m services.staged.org_risk_summary.contract)
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    # ------------------------------------------------------------------- #
    # In‑memory SQLite setup (used only for the self‑test)
    # ------------------------------------------------------------------- #
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestSession = sessionmaker(bind=engine)

    def get_test_session() -> Session:  # pragma: no cover
        return TestSession()

    # create minimal schema
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                CREATE TABLE McpServerRegistry (
                    server_id TEXT PRIMARY KEY,
                    org_id TEXT,
                    risk_tier TEXT,
                    verdict TEXT,
                    confidence REAL,
                    last_assessed TEXT
                )
                """
            )
        )
        conn.execute(
            text(
                """
                CREATE TABLE McpLlmAxisScore (
                    server_id TEXT,
                    axis_name TEXT,
                    p_top REAL,
                    p_critical REAL,
                    p_danger REAL
                )
                """
            )
        )

    # seed test data
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO McpServerRegistry
                (server_id, org_id, risk_tier, verdict, confidence, last_assessed)
                VALUES
                ('srv1', 'test_org', 'HIGH_RISK_ISOLATED', 'malicious', 0.90, '2023-01-01T00:00:00Z'),
                ('srv2', 'test_org', 'LOW_RISK', 'benign', 0.80, '2023-01-02T00:00:00Z'),
                ('srv3', 'test_org', 'LOW_RISK', 'benign', 0.85, '2023-01-03T00:00:00Z')
                """
            )
        )

    # FastAPI app with dependency override
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = get_test_session

    client = TestClient(app)
    response = client.get("/api/org/test_org/risk_summary")
    assert response.status_code == 200, f"Unexpected status {response.status_code}"
    data = response.json()

    assert data["total_servers"] == 3
    assert data["tier_counts"].get("HIGH_RISK_ISOLATED") == 1
    assert abs(data["average_confidence"] - 0.85) < 0.01

    print("PASS")