"""services/staged/trust_score_distribution_api/router.py"""

from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from app.db import get_session
from sqlalchemy.orm import Session

# Import the real business logic
from .logic import get_trust_score_distribution

router = APIRouter(prefix="/api", tags=["trust_score_distribution"])


class Bucket(BaseModel):
    lower: float = Field(..., description="Inclusive lower bound of the bucket")
    upper: float = Field(..., description="Exclusive upper bound of the bucket")
    count: int = Field(..., description="Number of servers in this bucket")


class ByTier(BaseModel):
    risk_tier: str = Field(..., description="Risk tier name")
    count: int = Field(..., description="Number of servers in this tier")


class TrustScoreDistributionResponse(BaseModel):
    bucket_size: int = Field(..., description="Width of each bucket")
    total: int = Field(..., description="Total number of servers")
    scored: int = Field(..., description="Number of servers with a trust_score")
    unscored: int = Field(..., description="Number of servers without a trust_score")
    buckets: List[Bucket] = Field(..., description="Histogram buckets")
    by_tier: List[ByTier] = Field(..., description="Counts per risk tier")
    generated_at: datetime = Field(..., description="Timestamp of generation")


@router.get(
    "/servers/trust-distribution",
    response_model=TrustScoreDistributionResponse,
    summary="Get trust‑score distribution histogram",
)
def trust_score_distribution(
    source: Optional[str] = Query(
        None, description="Filter by registry_source"
    ),
    bucket_size: int = Query(
        10,
        ge=1,
        description="Width of each histogram bucket (default 10)",
    ),
    session: Session = Depends(get_session),
):
    """
    Return a histogram of server trust scores, optionally filtered by
    `registry_source`.  Buckets are fixed‑width and cover the range 0‑100,
    including empty buckets so the client can render a full histogram.
    """
    return get_trust_score_distribution(
        session=session, source=source, bucket_size=bucket_size
    )


# --------------------------------------------------------------------------- #
# Self‑test (executed when running this module directly)
# --------------------------------------------------------------------------- #
if __name__ == "__main__":  # pragma: no cover
    import sys
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    # Build an in‑memory SQLite DB using the real models
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    # Import the declarative base (metadata) from the models module
    try:
        from app.models import Base  # type: ignore
    except Exception:  # pragma: no cover
        # Fallback: use the metadata attached to any model
        from app.models import McpServerRegistry

        Base = McpServerRegistry.__table__.metadata

    Base.metadata.create_all(engine)

    SessionLocal = sessionmaker(bind=engine)

    # Dependency override that yields a session bound to the in‑memory DB
    def override_get_session() -> Session:
        db = SessionLocal()
        try:
            yield db
        finally:
            db.close()

    # Populate test data
    from app.models import McpServerRegistry

    test_rows = [
        {
            "server_id": "srv-1",
            "name": "Server One",
            "registry_source": "src-A",
            "risk_tier": "high",
            "verdict": "malicious",
            "confidence": 0.9,
            "trust_score": 5.0,
        },
        {
            "server_id": "srv-2",
            "name": "Server Two",
            "registry_source": "src-A",
            "risk_tier": "medium",
            "verdict": "suspicious",
            "confidence": 0.8,
            "trust_score": 42.0,
        },
        {
            "server_id": "srv-3",
            "name": "Server Three",
            "registry_source": "src-B",
            "risk_tier": "low",
            "verdict": "benign",
            "confidence": 0.95,
            "trust_score": 95.0,
        },
        {
            "server_id": "srv-4",
            "name": "Server Four",
            "registry_source": "src-B",
            "risk_tier": "low",
            "verdict": "benign",
            "confidence": 0.95,
            "trust_score": None,
        },
    ]

    with SessionLocal() as db:
        for row in test_rows:
            db.add(McpServerRegistry(**row))
        db.commit()

    # Assemble FastAPI app with router and override
    app = FastAPI()
    app.dependency_overrides[get_session] = override_get_session
    app.include_router(router)

    client = TestClient(app)

    resp = client.get("/api/servers/trust-distribution?bucket_size=10")
    if resp.status_code != 200:
        print(f"FAIL: unexpected status {resp.status_code}", file=sys.stderr)
        sys.exit(1)

    data = resp.json()
    # Basic contract assertions
    assert data["scored"] == 3, "scored count mismatch"
    assert data["unscored"] == 1, "unscored count mismatch"
    assert data["bucket_size"] == 10, "bucket size mismatch"

    # Sum of bucket counts must equal scored
    bucket_total = sum(b["count"] for b in data["buckets"])
    assert bucket_total == data["scored"], "bucket total mismatch"

    # Buckets must be strictly ascending by lower bound
    lowers = [b["lower"] for b in data["buckets"]]
    assert lowers == sorted(lowers) and len(set(lowers)) == len(lowers), "bucket ordering invalid"

    # by_tier must contain at least one entry with a risk_tier key
    assert any("risk_tier" in tier for tier in data["by_tier"]), "by_tier missing risk_tier"

    print("PASS")