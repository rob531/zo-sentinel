# deps: fastapi, pydantic, sqlalchemy

from fastapi import HTTPException
from sqlalchemy.orm import Session
from typing import Any

from app.models import McpLlmAxisScore, McpServerRegistry


def get_server_detail(server_id: str, session: Session) -> dict[str, Any]:
    """Fetch server record and axis scores; apply CRITICAL escalation override."""
    server = (
        session.query(McpServerRegistry)
        .filter(McpServerRegistry.server_id == server_id)
        .first()
    )
    if not server:
        raise HTTPException(status_code=404, detail="Server not found")

    axes_q = (
        session.query(McpLlmAxisScore)
        .filter(McpLlmAxisScore.server_id == server_id)
        .order_by(McpLlmAxisScore.scored_at.desc())
        .all()
    )

    axes_dict: dict[str, dict[str, Any]] = {}
    override_tier: str | None = None
    for row in axes_q:
        if row.axis_name not in axes_dict:
            axes_dict[row.axis_name] = {
                "label": row.label,
                "p_top": row.p_top,
                "escalated": bool(row.escalated),
                "escalated_to": row.escalated_to,
            }
        if row.escalated and row.escalated_to == "CRITICAL" and override_tier is None:
            override_tier = "CRITICAL"

    return {
        "server": {
            "server_id": server.server_id,
            "name": server.name,
            "url": server.url,
            "description": server.description,
            "risk_tier": server.risk_tier,
            "verdict": server.verdict,
            "verdict_reasoning": server.verdict_reasoning,
            "trust_score": server.trust_score,
            "confidence": server.confidence,
            "last_seen": str(server.last_seen) if server.last_seen else None,
            "last_scanned": str(server.last_scanned) if server.last_scanned else None,
            "last_assessed": str(server.last_assessed) if server.last_assessed else None,
            "scan_count": server.scan_count,
            "first_seen": str(server.first_seen) if server.first_seen else None,
            "registry_source": server.registry_source,
            "meta": server.meta,
        },
        "axes": axes_dict,
        "override_tier": override_tier,
    }


if __name__ == "__main__":
    import sys
    from datetime import datetime
    from pathlib import Path

    _root = Path(__file__).resolve().parents[3]
    if str(_root) not in sys.path:
        sys.path.insert(0, str(_root))

    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    from app.models import Base
    from app.db import get_session

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    TestSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    def get_test_session():
        db = TestSessionLocal()
        try:
            yield db
        finally:
            db.close()

    with TestSessionLocal() as db:
        now = datetime.utcnow()
        db.add(
            McpServerRegistry(
                server_id="test-srv-001",
                name="Test Server",
                url="https://example.com",
                description="A test server",
                risk_tier="LOW",
                verdict="CLEAR",
                verdict_reasoning="Test reasoning",
                trust_score=0.9,
                confidence=0.8,
                scan_count=1,
                registry_source="unit-test",
                first_seen=now,
                last_seen=now,
                last_scanned=now,
                last_assessed=now,
            )
        )
        axis_data = [
            ("overall_risk", "medium", 0.72, False, None),
            ("auth_strength", "strong", 0.88, False, None),
            ("capability_breadth", "high", 0.81, True, "CRITICAL"),
            ("data_exposure", "low", 0.95, False, None),
            ("supply_chain", "medium", 0.65, False, None),
            ("operational_security", "high", 0.78, False, None),
            ("compliance_posture", "medium", 0.71, False, None),
        ]
        for idx, (axis, lbl, p_top, esc, esc_to) in enumerate(axis_data):
            db.add(
                McpLlmAxisScore(
                    server_id="test-srv-001",
                    axis_name=axis,
                    label=lbl,
                    label_index=idx,
                    p_top=p_top,
                    p_critical=0.05,
                    p_danger=0.15,
                    escalated=esc,
                    escalated_to=esc_to,
                    model_version="v1",
                    decision_rule_version="r1",
                    adapter_sha256="deadbeef",
                    scored_at=now,
                )
            )
        db.commit()

    test_app = FastAPI()

    @test_app.get("/api/servers/{server_id}")
    def _endpoint(server_id: str, session: Session = Depends(get_session)):
        return get_server_detail(server_id, session)

    test_app.dependency_overrides[get_session] = get_test_session

    client = TestClient(test_app)

    # Happy path
    resp = client.get("/api/servers/test-srv-001")
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
    data = resp.json()
    assert "server" in data
    assert data["server"]["server_id"] == "test-srv-001"
    assert data["server"]["risk_tier"] == "LOW"
    assert "axes" in data
    assert isinstance(data["axes"], dict)
    assert len(data["axes"]) == 7, f"Expected 7 axes, got {len(data['axes'])}"
    assert data["override_tier"] == "CRITICAL", f"Expected CRITICAL override, got {data['override_tier']}"
    assert data["axes"]["overall_risk"]["label"] == "medium"
    assert data["axes"]["overall_risk"]["p_top"] == 0.72

    # Not-found path
    resp_404 = client.get("/api/servers/nonexistent")
    assert resp_404.status_code == 404, f"Expected 404, got {resp_404.status_code}"

    print("PASS")
    sys.exit(0)
