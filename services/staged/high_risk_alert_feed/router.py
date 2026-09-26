"""HTTP surface for the high-risk alert feed."""
from __future__ import annotations

from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session

from .logic import get_high_risk_alerts as query_high_risk_alerts

router = APIRouter(prefix="/api", tags=["high_risk_alert_feed"])


class HighRiskAlert(BaseModel):
    server_id: str
    name: Optional[str] = None
    url: Optional[str] = None
    verdict: Optional[str] = None
    risk_tier: Optional[str] = None
    last_assessed: Optional[datetime] = None
    cve_summary: Optional[str] = None
    cve_critical_count: Optional[int] = None
    cve_high_count: Optional[int] = None
    confidence: Optional[float] = None


@router.get("/alerts/high-risk", response_model=List[HighRiskAlert])
def get_high_risk_alerts(
    hours: int = Query(default=24, ge=1),
    db: Session = Depends(get_session),
) -> List[HighRiskAlert]:
    return query_high_risk_alerts(hours=hours, db=db)


if __name__ == "__main__":
    import sys

    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.ext.compiler import compiles
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    from sqlalchemy.sql.functions import Function

    from app.db import Base
    from app.models import McpLlmAxisScore, McpServerRegistry

    @compiles(Function, "sqlite")
    def compile_sqlite_function(element, compiler, **kwargs):
        if element.name.lower() == "null":
            return "NULL"
        return compiler.visit_function(element, **kwargs)

    test_engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=test_engine)
    TestSession = sessionmaker(bind=test_engine, autoflush=False, autocommit=False)

    now = datetime.utcnow()
    with TestSession() as db:
        db.add_all(
            [
                McpServerRegistry(
                    server_id="srv-a",
                    name="Alpha",
                    url="https://alpha.example.com",
                    verdict="SAFE",
                    risk_tier="LOW",
                    last_assessed=now - __import__("datetime").timedelta(hours=2),
                    confidence=0.91,
                ),
                McpServerRegistry(
                    server_id="srv-b",
                    name="Beta",
                    url="https://beta.example.com",
                    verdict="UNKNOWN",
                    risk_tier="MEDIUM",
                    last_assessed=now - __import__("datetime").timedelta(hours=1),
                    confidence=0.72,
                ),
                McpServerRegistry(
                    server_id="srv-c",
                    name="Gamma",
                    url="https://gamma.example.com",
                    verdict="MALICIOUS",
                    risk_tier="CRITICAL",
                    last_assessed=now,
                    confidence=0.98,
                ),
            ]
        )
        db.add_all(
            [
                McpLlmAxisScore(
                    id=101,
                    server_id="srv-a",
                    axis_name="risk",
                    model_version="self-test-v1",
                    escalated=False,
                    scored_at=now,
                ),
                McpLlmAxisScore(
                    id=102,
                    server_id="srv-b",
                    axis_name="risk",
                    model_version="self-test-v1",
                    escalated=False,
                    scored_at=now,
                ),
                McpLlmAxisScore(
                    id=103,
                    server_id="srv-c",
                    axis_name="risk",
                    model_version="self-test-v1",
                    escalated=True,
                    escalated_to="CRITICAL",
                    scored_at=now,
                    p_critical=0.99,
                    p_danger=0.01,
                    p_top=0.99,
                ),
            ]
        )
        db.commit()

    def get_test_session():
        db = TestSession()
        try:
            yield db
        finally:
            db.close()

    test_app = FastAPI()
    test_app.dependency_overrides[get_session] = get_test_session
    test_app.include_router(router)

    try:
        response = TestClient(test_app).get("/api/alerts/high-risk?hours=24")
        assert response.status_code == 200, f"Unexpected status {response.status_code}: {response.text}"
        result = response.json()
        assert isinstance(result, list) and len(result) >= 1, "Expected a non-empty alert list"
        alert = next((item for item in result if item["server_id"] == "srv-c"), None)
        assert alert is not None, "Escalated server is missing"
        assert alert["risk_tier"] == "CRITICAL", f"Unexpected risk tier: {alert['risk_tier']}"
    except Exception as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc

    print("PASS")