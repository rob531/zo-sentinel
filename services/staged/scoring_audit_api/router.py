from __future__ import annotations

from datetime import datetime, timedelta

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry

from .logic import get_scoring_audit

router = APIRouter(prefix="/api", tags=["scoring-audit"])


@router.get("/scoring/audit/{server_id}")
def scoring_audit_endpoint(
    server_id: int,
    db: Session = Depends(get_session),
):
    return get_scoring_audit(server_id, db)


if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.db import Base

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestingSession = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    Base.metadata.create_all(bind=engine)

    axes = [
        "availability",
        "confidentiality",
        "integrity",
        "privacy",
        "reliability",
        "security",
        "usability",
    ]
    now = datetime.utcnow()

    with TestingSession() as db:
        db.add_all(
            [
                McpServerRegistry(
                    server_id="1",
                    name="alpha.example.com",
                    meta="{}",
                ),
                McpServerRegistry(
                    server_id="2",
                    name="beta.example.com",
                    meta="{}",
                ),
            ]
        )
        db.flush()

        score_id = 1
        for axis in axes:
            db.add(
                McpLlmAxisScore(
                    id=score_id,
                    server_id="1",
                    axis_name=axis,
                    label="low",
                    label_index=0,
                    model_version="model-test-v1",
                    adapter_sha256="a" * 64,
                    decision_rule_version="rule-test-v1",
                    p_top=0.8,
                    p_critical=0.1,
                    p_danger=0.1,
                    probs={"low": 0.8, "critical": 0.1, "danger": 0.1},
                    scored_at=now,
                )
            )
            score_id += 1

        db.add(
            McpLlmAxisScore(
                id=score_id,
                server_id="2",
                axis_name=axes[0],
                label="medium",
                label_index=1,
                model_version="model-test-v1",
                adapter_sha256="b" * 64,
                decision_rule_version="rule-test-v1",
                p_top=0.6,
                p_critical=0.2,
                p_danger=0.2,
                probs={"low": 0.2, "medium": 0.6, "critical": 0.2},
                scored_at=now + timedelta(minutes=1),
            )
        )
        db.commit()

    def get_test_session():
        with TestingSession() as db:
            yield db

    test_app = FastAPI()
    test_app.include_router(router)
    test_app.dependency_overrides[get_session] = get_test_session

    with TestClient(test_app) as client:
        response = client.get("/api/scoring/audit/1")

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["server_name"] == "alpha.example.com", payload
    assert payload["model_version"], payload
    assert len(payload["axes"]) == 7, payload
    print("PASS")