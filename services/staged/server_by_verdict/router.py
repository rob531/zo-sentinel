from fastapi import APIRouter, Depends, Query
from sqlalchemy import func
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool
from fastapi.testclient import TestClient
from typing import Optional

from app.db import get_session
from app.models import McpServerRegistry

router = APIRouter(prefix="/api/servers", tags=["servers"])


@router.get("/by-verdict")
def get_servers_by_verdict(
    verdict: str = Query(..., description="Verdict to filter by"),
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    session: Session = Depends(get_session),
):
    query = session.query(McpServerRegistry).filter(McpServerRegistry.verdict == verdict)
    total = query.count()
    servers = query.offset(offset).limit(limit).all()

    return {
        "verdict": verdict,
        "total": total,
        "servers": [
            {
                "server_id": s.server_id,
                "name": s.name,
                "risk_tier": s.risk_tier,
                "trust_score": s.trust_score,
                "last_assessed": s.last_assessed,
                "url": s.url,
            }
            for s in servers
        ],
    }


if __name__ == "__main__":
    from fastapi import FastAPI
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    from app.models import Base

    Base.metadata.create_all(bind=engine)
    session = SessionLocal()

    from datetime import datetime, timezone

    seed_data = [
        McpServerRegistry(
            server_id="srv_001",
            name="Trusted General Alpha",
            verdict="TRUSTED_GENERAL",
            risk_tier="LOW",
            trust_score=0.95,
            last_assessed=datetime.now(timezone.utc),
            url="https://trusted-alpha.example.com",
            description="General purpose trusted server",
            first_seen=datetime.now(timezone.utc),
            registry_source="test",
        ),
        McpServerRegistry(
            server_id="srv_002",
            name="Trusted General Beta",
            verdict="TRUSTED_GENERAL",
            risk_tier="LOW",
            trust_score=0.92,
            last_assessed=datetime.now(timezone.utc),
            url="https://trusted-beta.example.com",
            description="Another general purpose trusted server",
            first_seen=datetime.now(timezone.utc),
            registry_source="test",
        ),
        McpServerRegistry(
            server_id="srv_003",
            name="Trusted Research Gamma",
            verdict="TRUSTED_RESEARCH",
            risk_tier="MEDIUM",
            trust_score=0.85,
            last_assessed=datetime.now(timezone.utc),
            url="https://research-gamma.example.com",
            description="Research-oriented server",
            first_seen=datetime.now(timezone.utc),
            registry_source="test",
        ),
        McpServerRegistry(
            server_id="srv_004",
            name="Trusted Research Delta",
            verdict="TRUSTED_RESEARCH",
            risk_tier="MEDIUM",
            trust_score=0.88,
            last_assessed=datetime.now(timezone.utc),
            url="https://research-delta.example.com",
            description="Another research-oriented server",
            first_seen=datetime.now(timezone.utc),
            registry_source="test",
        ),
        McpServerRegistry(
            server_id="srv_005",
            name="High Risk Isolated Epsilon",
            verdict="HIGH_RISK_ISOLATED",
            risk_tier="CRITICAL",
            trust_score=0.15,
            last_assessed=datetime.now(timezone.utc),
            url="https://high-risk-epsilon.example.com",
            description="High risk isolated server",
            first_seen=datetime.now(timezone.utc),
            registry_source="test",
        ),
        McpServerRegistry(
            server_id="srv_006",
            name="High Risk Isolated Zeta",
            verdict="HIGH_RISK_ISOLATED",
            risk_tier="CRITICAL",
            trust_score=0.12,
            last_assessed=datetime.now(timezone.utc),
            url="https://high-risk-zeta.example.com",
            description="Another high risk isolated server",
            first_seen=datetime.now(timezone.utc),
            registry_source="test",
        ),
    ]

    for srv in seed_data:
        session.add(srv)
    session.commit()

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = lambda: session
    client = TestClient(app)

    verdicts_to_test = [
        ("TRUSTED_GENERAL", 2),
        ("TRUSTED_RESEARCH", 2),
        ("HIGH_RISK_ISOLATED", 2),
    ]

    for verdict, expected_count in verdicts_to_test:
        response = client.get(f"/api/servers/by-verdict?verdict={verdict}")
        assert response.status_code == 200, f"Expected 200 for verdict {verdict}"
        data = response.json()
        assert data["total"] == expected_count, f"Expected {expected_count} servers for verdict {verdict}, got {data['total']}"
        assert len(data["servers"]) == expected_count, f"Expected {expected_count} servers in list for verdict {verdict}"

    print("PASS")