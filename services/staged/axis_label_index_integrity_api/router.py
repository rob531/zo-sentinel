from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore

from .logic import audit_label_indexes

router = APIRouter(prefix="/api", tags=["axis-label-integrity"])


@router.get("/scores/axis-label-integrity")
def get_axis_label_index_integrity(
    session: Session = Depends(get_session),
) -> dict:
    rows = session.execute(
        select(
            McpLlmAxisScore.axis_name,
            McpLlmAxisScore.label,
            McpLlmAxisScore.label_index,
            McpLlmAxisScore.scored_at,
            McpLlmAxisScore.server_id,
        ).order_by(
            McpLlmAxisScore.server_id,
            McpLlmAxisScore.axis_name,
            McpLlmAxisScore.scored_at,
            McpLlmAxisScore.label_index,
        )
    ).mappings()

    result = audit_label_indexes([dict(row) for row in rows])
    return {
        **result,
        "checked_axes": len(result["axes"]),
    }


if __name__ == "__main__":
    from datetime import datetime

    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine, insert
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    that_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    with that_engine.begin() as connection:
        connection.exec_driver_sql(
            """
            CREATE TABLE mcp_llm_axis_scores (
                axis_name VARCHAR NOT NULL,
                label VARCHAR NOT NULL,
                label_index INTEGER NOT NULL,
                scored_at DATETIME,
                server_id VARCHAR NOT NULL
            )
            """
        )

    that_session_factory = sessionmaker(bind=that_engine)
    with that_session_factory() as that_session:
        that_session.execute(
            insert(McpLlmAxisScore.__table__),
            [
                {
                    "axis_name": "overall_risk",
                    "label": f"risk-{index}",
                    "label_index": index,
                    "scored_at": datetime(2024, 1, 1),
                    "server_id": "server-full",
                }
                for index in range(4)
            ]
            + [
                {
                    "axis_name": "auth_strength",
                    "label": "auth-0",
                    "label_index": 0,
                    "scored_at": datetime(2024, 1, 2),
                    "server_id": "server-broken",
                },
                {
                    "axis_name": "auth_strength",
                    "label": "auth-0-duplicate",
                    "label_index": 0,
                    "scored_at": datetime(2024, 1, 3),
                    "server_id": "server-broken",
                },
                {
                    "axis_name": "auth_strength",
                    "label": "auth-2",
                    "label_index": 2,
                    "scored_at": datetime(2024, 1, 4),
                    "server_id": "server-broken",
                },
                {
                    "axis_name": "unexpected_axis",
                    "label": "unexpected-0",
                    "label_index": 0,
                    "scored_at": datetime(2024, 1, 5),
                    "server_id": "server-unknown",
                },
            ],
        )
        that_session.commit()

    that_app = FastAPI()
    that_app.include_router(router)
    that_app.dependency_overrides[get_session] = lambda: that_session_factory()

    with TestClient(that_app) as that_client:
        that_response = that_client.get("/api/scores/axis-label-integrity")
        assert that_response.status_code == 200
        that_payload = that_response.json()
        assert len(that_payload["axes"]) == 3
        assert that_payload["checked_axes"] == 3

        that_unknown = next(
            axis
            for axis in that_payload["axes"]
            if axis["axis_name"] == "unexpected_axis"
        )
        assert that_unknown["expected_axis"] is False

        that_broken = next(
            axis
            for axis in that_payload["axes"]
            if axis["axis_name"] == "auth_strength"
        )
        assert that_broken["duplicate_indexes"]
        assert that_broken["missing_indexes"]

    that_engine.dispose()
    print("PASS")