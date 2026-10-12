from datetime import date, timedelta
from typing import List

from fastapi import Depends
from pydantic import BaseModel
from sqlalchemy import func, text
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry


class SeriesItem(BaseModel):
    date: str
    label: str
    count: int
    p_top_avg: float


class NetworkEgressTrendResponse(BaseModel):
    days: int
    series: List[SeriesItem]


def get_network_egress_trend(days: int, session: Session) -> NetworkEgressTrendResponse:
    cutoff = date.today() - timedelta(days=days)
    query = (
        session.query(
                func.date(McpLlmAxisScore.scored_at).label("date"),
                McpLlmAxisScore.label,
                func.count(McpLlmAxisScore.server_id).label("count"),
                func.avg(McpLlmAxisScore.p_top).label("p_top_avg"),
            )
            .join(
                McpServerRegistry,
                McpLlmAxisScore.server_id == McpServerRegistry.server_id,
            )
            .filter(McpLlmAxisScore.axis_name == "network_egress")
            .filter(func.date(McpLlmAxisScore.scored_at) >= cutoff)
            .group_by(text("date"), McpLlmAxisScore.label)
            .order_by(text("date"), McpLlmAxisScore.label)
        )
    series = [
        SeriesItem(
            date=str(row.date),
            label=row.label,
            count=row.count,
            p_top_avg=row.p_top_avg,
        )
        for row in query.all()
    ]
    return NetworkEgressTrendResponse(days=days, series=series)


if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestingSessionLocal = sessionmaker(bind=engine)

    def override_get_session():
        session = TestingSessionLocal()
        try:
            yield session
        finally:
            session.close()

    with engine.begin() as conn:
        conn.execute(
            text(
                "CREATE TABLE mcp_server_registry ("
                "server_id TEXT PRIMARY KEY,"
                "name TEXT NOT NULL,"
                "risk_tier TEXT"
                ")"
            )
        )
        conn.execute(
            text(
                "CREATE TABLE mcp_llm_axis_scores ("
                "id INTEGER PRIMARY KEY,"
                "server_id TEXT NOT NULL,"
                "axis_name TEXT NOT NULL,"
                "label TEXT NOT NULL,"
                "label_index INTEGER,"
                "p_critical REAL,"
                "p_danger REAL,"
                "p_top REAL,"
                "scored_at TIMESTAMP NOT NULL"
                ")"
            )
        )

    session = TestingSessionLocal()
    session.execute(
        text("INSERT INTO mcp_server_registry VALUES ('s1', 'Server One', 'medium')")
    )
    session.execute(
        text("INSERT INTO mcp_server_registry VALUES ('s2', 'Server Two', 'medium')")
    )
    session.execute(
        text("INSERT INTO mcp_server_registry VALUES ('s3', 'Server Three', 'medium')")
    )
    d1 = date.today()
    d2 = date.today() - timedelta(days=1)
    session.execute(
        text(
            "INSERT INTO mcp_llm_axis_scores VALUES (1, 's1', 'network_egress', 'low', 3, 0.1, 0.2, 0.4, :d1)"
        ).bindparams(d1=d1)
    )
    session.execute(
        text(
            "INSERT INTO mcp_llm_axis_scores VALUES (2, 's2', 'network_egress', 'medium', 2, 0.2, 0.3, 0.6, :d1)"
        ).bindparams(d1=d1)
    )
    session.execute(
        text(
            "INSERT INTO mcp_llm_axis_scores VALUES (3, 's3', 'network_egress', 'high', 1, 0.3, 0.4, 0.7, :d2)"
        ).bindparams(d2=d2)
    )
    session.execute(
        text(
            "INSERT INTO mcp_llm_axis_scores VALUES (4, 's1', 'network_egress', 'medium', 2, 0.2, 0.3, 0.55, :d2)"
        ).bindparams(d2=d2)
    )
    session.execute(
        text(
            "INSERT INTO mcp_llm_axis_scores VALUES (5, 's2', 'network_egress', 'low', 3, 0.1, 0.2, 0.45, :d2)"
        ).bindparams(d2=d2)
    )
    session.execute(
        text(
            "INSERT INTO mcp_llm_axis_scores VALUES (6, 's3', 'network_egress', 'high', 1, 0.3, 0.4, 0.75, :d1)"
        ).bindparams(d1=d1)
    )
    session.commit()
    session.close()

    from main import app

    app.dependency_overrides[get_session] = override_get_session
    client = TestClient(app)

    resp = client.get("/api/trend/network_egress?days=10")
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}"
    data = resp.json()
    assert data["series"], "series should be non-empty"
    bucket = next(
        (s for s in data["series"] if s["label"] == "medium" and s["count"] == 1),
        None,
    )
    assert bucket is not None, "Expected at least one bucket with label=medium, count=1"

    print("PASS")