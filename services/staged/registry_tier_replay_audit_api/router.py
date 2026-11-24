"""HTTP surface for registry tier replay audits."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry

from .logic import get_tier_replay_audit

router = APIRouter(prefix="/api", tags=["registry-tier-replay-audit"])


@router.get("/audit/tier-replay")
def tier_replay_audit(
    db: Session = Depends(get_session),
) -> dict[str, Any]:
    return get_tier_replay_audit(db)


def _self_test() -> None:
    import uuid
    from datetime import datetime, timezone

    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import Integer, create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    McpServerRegistry.metadata.create_all(engine)
    TestingSession = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    score_id_type = McpLlmAxisScore.__table__.columns["id"].type
    score_id = 1 if isinstance(score_id_type, Integer) else str(uuid.uuid4())

    with TestingSession() as db:
        db.add_all(
            [
                McpServerRegistry(
                    server_id="registry-mismatch",
                    name="Mismatch Server",
                    risk_tier="TRUSTED",
                    verdict="allow",
                    trust_score=0.9,
                    last_assessed=datetime.now(timezone.utc),
                    last_seen=datetime.now(timezone.utc),
                ),
                McpServerRegistry(
                    server_id="registry-agree",
                    name="Agreeing Server",
                    risk_tier="TRUSTED",
                    verdict="allow",
                    trust_score=0.8,
                    last_assessed=datetime.now(timezone.utc),
                    last_seen=datetime.now(timezone.utc),
                ),
                McpServerRegistry(
                    server_id="registry-unscored",
                    name="Unscored Server",
                    risk_tier="CAUTION",
                    verdict="review",
                    trust_score=0.5,
                    last_assessed=datetime.now(timezone.utc),
                    last_seen=datetime.now(timezone.utc),
                ),
                McpLlmAxisScore(
                    id=score_id,
                    server_id="registry-mismatch",
                    axis_name="overall_risk",
                    label="high",
                    p_top=0.9,
                    p_critical=0.8,
                    decision_rule_version="test-v1",
                    model_version="test-model",
                    scored_at=datetime.now(timezone.utc),
                ),
                McpLlmAxisScore(
                    id=2 if isinstance(score_id_type, Integer) else str(uuid.uuid4()),
                    server_id="registry-agree",
                    axis_name="overall_risk",
                    label="trusted",
                    p_top=0.1,
                    p_critical=0.01,
                    decision_rule_version="test-v1",
                    model_version="test-model",
                    scored_at=datetime.now(timezone.utc),
                ),
            ]
        )
        db.commit()

    that_app = FastAPI()
    that_app.include_router(router)

    def override_get_session():
        with TestingSession() as db:
            yield db

    that_app.dependency_overrides[get_session] = override_get_session

    with TestClient(that_app) as client:
        response = client.get("/api/audit/tier-replay")
        assert response.status_code == 200
        payload = response.json()
        assert payload["totals"]["servers"] == 3
        assert payload["totals"]["unscored"] == 1
        assert "registry-mismatch" in {
            row["server_id"] for row in payload["mismatches"]
        }
        assert len(payload["mismatches"]) <= 200

    print("PASS")


if __name__ == "__main__":
    _self_test()