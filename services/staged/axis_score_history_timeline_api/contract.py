from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.testclient import TestClient
from pydantic import BaseModel
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import McpLlmAxisScore


VALID_AXES = {
    "overall_risk",
    "auth_strength",
    "capability_breadth",
    "data_sensitivity",
    "network_egress",
    "maintainer_trust",
    "exploit_surface",
}


class HistoryPoint(BaseModel):
    scored_at: datetime
    p_top: float
    p_critical: float
    p_danger: float
    escalated: bool
    escalated_to: Optional[str]
    model_version: Optional[str]
    decision_rule_version: Optional[str]


class HistoryResponse(BaseModel):
    server_id: str
    axis: Optional[str]
    count: int
    series: List[HistoryPoint]


def get_history(
    server_id: str = Query(...),
    axis: Optional[str] = Query(None),
    limit: int = Query(100, ge=1, le=1000),
    session: Session = Depends(get_session),
) -> HistoryResponse:
    if axis is not None and axis not in VALID_AXES:
        raise HTTPException(status_code=400, detail="Unknown axis")

    table = McpLlmAxisScore.__tablename__
    sql = text(
        f"""
        SELECT
            scored_at,
            p_top,
            p_critical,
            p_danger,
            escalated,
            escalated_to,
            model_version,
            decision_rule_version
        FROM {table}
        WHERE server_id = :server_id
        """
    )
    params: Dict[str, Any] = {"server_id": server_id}

    if axis is not None:
        sql = text(
            f"""
            SELECT
                scored_at,
                p_top,
                p_critical,
                p_danger,
                escalated,
                escalated_to,
                model_version,
                decision_rule_version
            FROM {table}
            WHERE server_id = :server_id
              AND axis_name = :axis_name
            """
        )
        params["axis_name"] = axis

    sql = text(
        str(sql)
        + " ORDER BY scored_at DESC LIMIT :limit"
    )
    params["limit"] = limit

    rows = session.execute(sql, params).mappings().all()
    series = [
        HistoryPoint(
            scored_at=row["scored_at"],
            p_top=row["p_top"],
            p_critical=row["p_critical"],
            p_danger=row["p_danger"],
            escalated=bool(row["escalated"]),
            escalated_to=row["escalated_to"],
            model_version=row["model_version"],
            decision_rule_version=row["decision_rule_version"],
        )
        for row in rows
    ]

    return HistoryResponse(
        server_id=server_id,
        axis=axis,
        count=len(series),
        series=series,
    )


def _make_test_app() -> FastAPI:
    application = FastAPI()
    application.get(
        "/api/axis/scores/history",
        response_model=HistoryResponse,
    )(get_history)
    return application


def _seed_database(engine: Any) -> None:
    table = McpLlmAxisScore.__tablename__
    with engine.begin() as connection:
        connection.execute(
            text(
                f"""
                CREATE TABLE {table} (
                    id INTEGER PRIMARY KEY,
                    server_id TEXT NOT NULL,
                    axis_name TEXT NOT NULL,
                    label TEXT,
                    label_index INTEGER,
                    probs TEXT,
                    p_top REAL,
                    p_critical REAL,
                    p_danger REAL,
                    escalated INTEGER NOT NULL DEFAULT 0,
                    escalated_to TEXT,
                    decision_rule_version TEXT,
                    model_version TEXT,
                    scored_at TEXT NOT NULL
                )
                """
            )
        )

        rows = []
        for server_id in ("server-1", "server-2", "server-3"):
            for axis_name in ("auth_strength", "capability_breadth"):
                for timestamp, p_top in (
                    ("2024-01-01T00:00:00", 0.8125),
                    ("2024-01-02T00:00:00", 0.9375),
                ):
                    rows.append(
                        {
                            "server_id": server_id,
                            "axis_name": axis_name,
                            "label": "seeded",
                            "label_index": 0,
                            "probs": "{}",
                            "p_top": p_top,
                            "p_critical": 0.125,
                            "p_danger": 0.25,
                            "escalated": 1,
                            "escalated_to": "review",
                            "decision_rule_version": "rules-v1",
                            "model_version": "model-v1",
                            "scored_at": timestamp,
                        }
                    )

        connection.execute(
            text(
                f"""
                INSERT INTO {table} (
                    server_id,
                    axis_name,
                    label,
                    label_index,
                    probs,
                    p_top,
                    p_critical,
                    p_danger,
                    escalated,
                    escalated_to,
                    decision_rule_version,
                    model_version,
                    scored_at
                ) VALUES (
                    :server_id,
                    :axis_name,
                    :label,
                    :label_index,
                    :probs,
                    :p_top,
                    :p_critical,
                    :p_danger,
                    :escalated,
                    :escalated_to,
                    :decision_rule_version,
                    :model_version,
                    :scored_at
                )
                """
            ),
            rows,
        )


def main() -> None:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    _seed_database(engine)

    session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    application = _make_test_app()

    def override_get_session():
        session = session_factory()
        try:
            yield session
        finally:
            session.close()

    application.dependency_overrides[get_session] = override_get_session

    with TestClient(application) as client:
        response = client.get(
            "/api/axis/scores/history",
            params={
                "server_id": "server-1",
                "axis": "auth_strength",
                "limit": 100,
            },
        )
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["server_id"] == "server-1"
        assert payload["axis"] == "auth_strength"
        assert payload["count"] == 2
        assert len(payload["series"]) == 2
        assert payload["series"][0]["p_top"] == 0.9375
        assert payload["series"][1]["p_top"] == 0.8125

        invalid_response = client.get(
            "/api/axis/scores/history",
            params={
                "server_id": "server-1",
                "axis": "not_an_axis",
            },
        )
        assert invalid_response.status_code == 400, invalid_response.text

    print("PASS")


if __name__ == "__main__":
    main()