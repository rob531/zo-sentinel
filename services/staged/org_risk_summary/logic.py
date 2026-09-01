import datetime
from collections import Counter
from typing import Dict, List

import requests
from fastapi import APIRouter, Depends, FastAPI, HTTPException
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore

router = APIRouter(prefix="/api")


def _org_column():
    """Return the column attribute on McpServerRegistry that stores the org identifier."""
    if hasattr(McpServerRegistry, "org_id"):
        return McpServerRegistry.org_id
    if hasattr(McpServerRegistry, "org"):
        return McpServerRegistry.org
    raise AttributeError("McpServerRegistry has no org identifier column")


def compute_org_risk_summary(org_id: str, db: Session) -> Dict:
    """Aggregate risk information for all servers belonging to an organization."""
    org_col = _org_column()
    servers = db.query(McpServerRegistry).filter(org_col == org_id).all()

    total_servers = len(servers)
    tier_counts: Dict[str, int] = Counter()
    confidence_sum = 0.0
    recent_verdicts: List[Dict] = []

    for srv in servers:
        tier = getattr(srv, "risk_tier", None)
        tier_counts[tier] += 1  # type: ignore[arg-type]

        confidence = getattr(srv, "confidence", 0.0)
        confidence_sum += confidence if confidence is not None else 0.0

        recent_verdicts.append(
            {
                "server_id": getattr(srv, "server_id"),
                "risk_tier": tier,
                "verdict": getattr(srv, "verdict", None),
                "assessed_at": (
                    getattr(srv, "last_assessed").isoformat()
                    if getattr(srv, "last_assessed")
                    else None
                ),
            }
        )

    average_confidence = confidence_sum / total_servers if total_servers else 0.0

    return {
        "org_id": org_id,
        "total_servers": total_servers,
        "tier_counts": dict(tier_counts),
        "average_confidence": round(average_confidence, 4),
        "recent_verdicts": recent_verdicts,
    }


@router.get("/org/{org_id}/risk_summary")
def get_org_risk_summary(org_id: str, db: Session = Depends(get_session)):
    """FastAPI endpoint exposing the aggregated risk summary."""
    try:
        return compute_org_risk_summary(org_id, db)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


# --------------------------------------------------------------------------- #
# Self‑test (executed when running this file directly)
# --------------------------------------------------------------------------- #
if __name__ == "__main__":

    # ----------------------------------------------------------------------- #
    # Create an in‑memory SQLite DB and override the app dependency
    # ----------------------------------------------------------------------- #
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.db import Base  # type: ignore

    TEST_ENGINE = create_engine("sqlite:///:memory:", echo=False)
    TestSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=TEST_ENGINE)

    Base.metadata.create_all(bind=TEST_ENGINE)

    def get_test_session() -> Session:  # pragma: no cover
        db = TestSessionLocal()
        try:
            yield db
        finally:
            db.close()

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = get_test_session

    # ----------------------------------------------------------------------- #
    # Seed test data
    # ----------------------------------------------------------------------- #
    with TestSessionLocal() as db:
        # Determine the correct org column name
        org_col_name = "org_id" if hasattr(McpServerRegistry, "org_id") else "org"

        server1 = McpServerRegistry(
            server_id="srv-1",
            **{org_col_name: "test_org"},
            risk_tier="HIGH_RISK_ISOLATED",
            verdict="malicious",
            confidence=0.9,
            last_assessed=datetime.datetime.utcnow(),
        )
        server2 = McpServerRegistry(
            server_id="srv-2",
            **{org_col_name: "test_org"},
            risk_tier="LOW_RISK",
            verdict="benign",
            confidence=0.8,
            last_assessed=datetime.datetime.utcnow(),
        )
        server3 = McpServerRegistry(
            server_id="srv-3",
            **{org_col_name: "test_org"},
            risk_tier="LOW_RISK",
            verdict="benign",
            confidence=0.85,
            last_assessed=datetime.datetime.utcnow(),
        )
        db.add_all([server1, server2, server3])
        db.commit()

    # ----------------------------------------------------------------------- #
    # Run the test client
    # ----------------------------------------------------------------------- #
    client = TestClient(app)
    resp = client.get("/api/org/test_org/risk_summary")
    assert resp.status_code == 200, f"Unexpected status {resp.status_code}"
    data = resp.json()

    assert data["total_servers"] == 3
    assert data["tier_counts"].get("HIGH_RISK_ISOLATED", 0) == 1
    # average confidence = (0.9 + 0.8 + 0.85) / 3 = 0.85
    assert abs(data["average_confidence"] - 0.85) < 1e-3

    print("PASS")