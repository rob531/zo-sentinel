from fastapi import APIRouter, Depends
from fastapi.testclient import TestClient
from pydantic import BaseModel
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker, Session
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry

router = APIRouter(prefix="/api")


class HeatmapAxis(BaseModel):
    axis_name: str
    label: str
    p_top: float
    p_critical: float
    p_danger: float
    scored_at: str


class HeatmapResponse(BaseModel):
    server_id: int
    axes: list[HeatmapAxis]


def get_heatmap(server_id: int, db: Session) -> HeatmapResponse:
    axes = (
        db.query(McpLlmAxisScore)
        .filter(McpLlmAxisScore.server_id == server_id)
        .order_by(McpLlmAxisScore.scored_at.desc())
        .all()
    )
    return HeatmapResponse(
        server_id=server_id,
        axes=[
            HeatmapAxis(
                axis_name=ax.axis_name,
                label=ax.label,
                p_top=ax.p_top,
                p_critical=ax.p_critical,
                p_danger=ax.p_danger,
                scored_at=ax.scored_at.isoformat() if ax.scored_at else "",
            )
            for ax in axes
        ],
    )


@router.get("/signal/heatmap", response_model=HeatmapResponse)
def get_signal_heatmap(server_id: int, db: Session = Depends(get_session)):
    return get_heatmap(server_id, db)


def create_app():
    from fastapi import FastAPI
    from fastapi.middleware.cors import CORSMiddleware

    app = FastAPI()
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(router)

    @app.get("/health")
    def health():
        return {"status": "ok"}

    @app.get("/.well-known/service-discovery")
    def discovery():
        return {"service": "signal_heatmap", "version": "1.0.0"}

    return app


if __name__ == "__main__":
    from fastapi import FastAPI

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SessionLocal = sessionmaker(bind=engine)

    with engine.begin() as conn:
        conn.execute(
            text(
                "CREATE TABLE mcp_server_registry (id INTEGER PRIMARY KEY, name TEXT, created_at TEXT)"
            )
        )
        conn.execute(
            text(
                "CREATE TABLE mcp_llm_axis_scores ("
                "id INTEGER PRIMARY KEY, server_id INTEGER, axis_name TEXT, label TEXT, "
                "p_top REAL, p_critical REAL, p_danger REAL, probs TEXT, "
                "adapter_sha256 TEXT, model_version TEXT, decision_rule_version TEXT, "
                "label_index INTEGER, escalated INTEGER, escalated_to TEXT, scored_at TEXT, "
                "FOREIGN KEY (server_id) REFERENCES mcp_server_registry(id)"
                ")"
            )
        )

    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO mcp_server_registry (id, name, created_at) VALUES (:id, :name, :created_at)"
            ),
            {"id": 1, "name": "test-server", "created_at": "2024-01-01T00:00:00"},
        )

        axes_data = [
            {"server_id": 1, "axis_name": "overall_risk", "label": "Overall Risk", "p_top": 0.85, "p_critical": 0.10, "p_danger": 0.05, "scored_at": "2024-01-15T10:00:00"},
            {"server_id": 1, "axis_name": "auth_strength", "label": "Auth Strength", "p_top": 0.70, "p_critical": 0.20, "p_danger": 0.10, "scored_at": "2024-01-15T10:00:00"},
            {"server_id": 1, "axis_name": "capability_breadth", "label": "Capability Breadth", "p_top": 0.60, "p_critical": 0.25, "p_danger": 0.15, "scored_at": "2024-01-15T10:00:00"},
            {"server_id": 1, "axis_name": "data_sensitivity", "label": "Data Sensitivity", "p_top": 0.50, "p_critical": 0.30, "p_danger": 0.20, "scored_at": "2024-01-15T10:00:00"},
            {"server_id": 1, "axis_name": "network_egress", "label": "Network Egress", "p_top": 0.75, "p_critical": 0.15, "p_danger": 0.10, "scored_at": "2024-01-15T10:00:00"},
            {"server_id": 1, "axis_name": "maintainer_trust", "label": "Maintainer Trust", "p_top": 0.80, "p_critical": 0.12, "p_danger": 0.08, "scored_at": "2024-01-15T10:00:00"},
            {"server_id": 1, "axis_name": "exploit_surface", "label": "Exploit Surface", "p_top": 0.65, "p_critical": 0.22, "p_danger": 0.13, "scored_at": "2024-01-15T10:00:00"},
        ]
        for i, ax in enumerate(axes_data):
            conn.execute(
                text(
                    "INSERT INTO mcp_llm_axis_scores (id, server_id, axis_name, label, p_top, p_critical, p_danger, probs, adapter_sha256, model_version, decision_rule_version, label_index, escalated, escalated_to, scored_at) VALUES (:id, :server_id, :axis_name, :label, :p_top, :p_critical, :p_danger, '[]', 'abc123', 'v1', 'r1', 0, 0, '', :scored_at)"
                ),
                {"id": i + 1, **ax},
            )

    app = create_app()

    def override_get_session():
        db = SessionLocal()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_session] = override_get_session

    client = TestClient(app)
    response = client.get("/api/signal/heatmap", params={"server_id": 1})
    assert response.status_code == 200
    data = response.json()
    assert len(data["axes"]) == 7
    assert data["axes"][0]["p_top"] == 0.85

    health_resp = client.get("/health")
    assert health_resp.status_code == 200

    print("PASS")