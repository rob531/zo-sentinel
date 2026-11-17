from fastapi import APIRouter, Depends, FastAPI, Query
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool
from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry

CANONICAL_AXES = (
    "overall_risk",
    "auth_strength",
    "capability_breadth",
    "data_sensitivity",
    "network_egress",
    "maintainer_trust",
    "exploit_surface",
)

router = APIRouter(prefix="/api/scoring")


@router.get("/server-axis-completeness")
def get_server_axis_completeness(
    limit: int = Query(default=50, ge=1, le=500),
    session: Session = Depends(get_session),
):
    registry_rows = session.execute(
        select(
            McpServerRegistry.server_id,
            McpServerRegistry.name,
            McpServerRegistry.risk_tier,
        ).order_by(McpServerRegistry.server_id)
    ).all()

    score_rows = session.execute(
        select(
            McpLlmAxisScore.server_id,
            McpLlmAxisScore.axis_name,
            McpLlmAxisScore.scored_at,
            McpLlmAxisScore.p_top,
        ).where(McpLlmAxisScore.axis_name.in_(CANONICAL_AXES))
    ).all()

    axes_by_server: dict[str, set[str]] = {}
    for server_id, axis_name, _scored_at, _p_top in score_rows:
        axes_by_server.setdefault(server_id, set()).add(axis_name)

    complete = 0
    incomplete_servers = []

    for server_id, name, risk_tier in registry_rows:
        axes_present = axes_by_server.get(server_id, set())
        axes_missing = sorted(set(CANONICAL_AXES) - axes_present)

        if not axes_missing:
            complete += 1
            continue

        incomplete_servers.append(
            {
                "server_id": server_id,
                "name": name,
                "risk_tier": risk_tier,
                "axes_present": len(axes_present),
                "axes_missing": axes_missing,
            }
        )

    incomplete_servers.sort(
        key=lambda item: (item["axes_present"], str(item["server_id"]))
    )
    total_servers = len(registry_rows)

    return {
        "total_servers": total_servers,
        "complete": complete,
        "incomplete": total_servers - complete,
        "completeness_rate": complete / total_servers if total_servers else 0.0,
        "incomplete_servers": incomplete_servers[:limit],
    }


def main() -> None:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    test_session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    session = test_session_factory()

    session.execute(
        text(
            """
            CREATE TABLE mcp_server_registry (
                server_id VARCHAR PRIMARY KEY,
                name VARCHAR,
                risk_tier VARCHAR
            )
            """
        )
    )
    session.execute(
        text(
            """
            CREATE TABLE mcp_llm_axis_scores (
                id INTEGER PRIMARY KEY,
                server_id VARCHAR,
                axis_name VARCHAR,
                scored_at VARCHAR,
                p_top FLOAT
            )
            """
        )
    )

    session.execute(
        text(
            """
            INSERT INTO mcp_server_registry (server_id, name, risk_tier)
            VALUES (:server_id, :name, :risk_tier)
            """
        ),
        [
            {"server_id": "complete-server", "name": "Complete Server", "risk_tier": "low"},
            {"server_id": "partial-server", "name": "Partial Server", "risk_tier": "medium"},
            {"server_id": "empty-server", "name": "Empty Server", "risk_tier": "high"},
        ],
    )

    score_rows = [
        {
            "server_id": "complete-server",
            "axis_name": axis,
            "scored_at": "2025-01-01T00:00:00Z",
            "p_top": 0.9,
        }
        for axis in CANONICAL_AXES
    ]
    score_rows.extend(
        [
            {
                "server_id": "partial-server",
                "axis_name": axis,
                "scored_at": "2025-01-01T00:00:00Z",
                "p_top": 0.8,
            }
            for axis in CANONICAL_AXES[:3]
        ]
    )
    session.execute(
        text(
            """
            INSERT INTO mcp_llm_axis_scores
                (server_id, axis_name, scored_at, p_top)
            VALUES
                (:server_id, :axis_name, :scored_at, :p_top)
            """
        ),
        score_rows,
    )
    session.commit()

    def override_get_session():
        yield session

    that_app = FastAPI()
    that_app.include_router(router)
    that_app.dependency_overrides[get_session] = override_get_session

    with TestClient(that_app) as client:
        response = client.get("/api/scoring/server-axis-completeness")
        assert response.status_code == 200
        payload = response.json()
        assert payload["total_servers"] == 3
        assert payload["complete"] == 1
        assert payload["incomplete"] == 2
        partial = next(
            server
            for server in payload["incomplete_servers"]
            if server["server_id"] == "partial-server"
        )
        assert len(partial["axes_missing"]) == 4

    session.close()
    engine.dispose()
    print("PASS")


if __name__ == "__main__":
    main()