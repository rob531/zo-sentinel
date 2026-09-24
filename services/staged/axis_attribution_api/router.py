from typing import List
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker, Session
from sqlalchemy.pool import StaticPool
from fastapi.testclient import TestClient

from app.db import get_session
from app.models import McpLlmAxisScore

router = APIRouter(prefix="/api", tags=["axis_attribution"])


class AxisAttributionItem(BaseModel):
    axis_name: str
    label: str
    p_top: float
    p_critical: float
    p_danger: float
    escalated: bool
    decision_rule_version: str
    model_version: str


class AxisAttributionResponse(BaseModel):
    data: List[AxisAttributionItem]
    axis_count: int


def get_axis_attribution(server_id: int, session: Session) -> List[McpLlmAxisScore]:
    return (
        session.query(McpLlmAxisScore)
        .filter(McpLlmAxisScore.server_id == server_id)
        .order_by(McpLlmAxisScore.p_top.desc())
        .all()
    )


@router.get(
    "/servers/{server_id}/axis-attribution",
    response_model=AxisAttributionResponse,
)
def get_axis_attribution_endpoint(
    server_id: int,
    session: Session = Depends(get_session),
) -> AxisAttributionResponse:
    axes = get_axis_attribution(server_id, session)
    items = [
        AxisAttributionItem(
            axis_name=ax.axis_name,
            label=ax.label,
            p_top=ax.p_top,
            p_critical=ax.p_critical,
            p_danger=ax.p_danger,
            escalated=ax.escalated,
            decision_rule_version=ax.decision_rule_version,
            model_version=ax.model_version,
        )
        for ax in axes
    ]
    return AxisAttributionResponse(data=items, axis_count=len(items))


if __name__ == "__main__":
    from fastapi import FastAPI

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    with engine.begin() as conn:
        conn.execute(
            text(
                """
            CREATE TABLE mcp_llm_axis_scores (
                id INTEGER PRIMARY KEY,
                server_id INTEGER NOT NULL,
                axis_name VARCHAR NOT NULL,
                label VARCHAR NOT NULL,
                p_top REAL NOT NULL,
                p_critical REAL NOT NULL,
                p_danger REAL NOT NULL,
                escalated BOOLEAN NOT NULL,
                decision_rule_version VARCHAR NOT NULL,
                model_version VARCHAR NOT NULL,
                probs TEXT,
                label_index INTEGER,
                adapter_sha256 VARCHAR,
                scored_at TIMESTAMP,
                escalated_to VARCHAR
            )
            """
            )
        )

    SessionLocal = sessionmaker(bind=engine)

    with SessionLocal() as session:
        axis_names = [
            "vulnerability",
            "velocity",
            "concentration",
            "network",
            "signal",
            "freshness",
            "history",
        ]
        for i, axis_name in enumerate(axis_names):
            session.add(
                McpLlmAxisScore(
                    id=i + 1,
                    server_id=1,
                    axis_name=axis_name,
                    label=f"label_{axis_name}",
                    p_top=0.5 + (i * 0.07),
                    p_critical=0.1,
                    p_danger=0.05,
                    escalated=False,
                    decision_rule_version="v1.0",
                    model_version="gpt-4",
                    probs="[0.5, 0.3, 0.2]",
                    label_index=0,
                    adapter_sha256="abc123",
                    scored_at=None,
                    escalated_to=None,
                )
            )
        session.commit()

    that_app = FastAPI()
    that_app.include_router(router)

    def override_get_session():
        db = SessionLocal()
        try:
            yield db
        finally:
            db.close()

    that_app.dependency_overrides[get_session] = override_get_session

    client = TestClient(that_app)
    response = client.get("/api/servers/1/axis-attribution")

    assert response.status_code == 200, f"Expected 200, got {response.status_code}"
    data = response.json()
    assert len(data["data"]) == 7, f"Expected 7 axes, got {len(data['data'])}"
    assert data["data"][0]["label"] is not None, "First axis label is null"

    print("PASS")