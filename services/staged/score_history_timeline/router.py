from datetime import datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, declarative_base

from app.db import get_session

router = APIRouter(prefix="/api", tags=["score_history_timeline"])

Base = declarative_base()


class MCPServerRegistry(Base):
    __tablename__ = "mcp_server_registry"

    id = __tablename__
    server_id = Column(String, primary_key=True)
    name = Column(String)
    created_at = Column(DateTime)


class MCPLLMAxisScore(Base):
    __tablename__ = "mcp_llm_axis_scores"

    id = Column(Integer, primary_key=True, autoincrement=True)
    server_id = Column(String, index=True)
    scored_at = Column(DateTime, index=True)
    axis_label = Column(String)
    p_top = Column(Float)
    p_critical = Column(Float)
    p_danger = Column(Float)


from sqlalchemy import Column, DateTime, Float, Integer, String


class ScoreHistoryResponse(BaseModel):
    server_id: str
    days: int
    series: list["DayScores"]


class DayScores(BaseModel):
    date: str
    scores: dict[str, Any]


class AxisScoreData(BaseModel):
    label: str
    p_top: float
    p_critical: float
    p_danger: float


@router.get("/servers/{server_id}/score-history", response_model=ScoreHistoryResponse)
def get_score_history(
    server_id: str,
    days: int = 7,
    session: Session = Depends(get_session),
) -> ScoreHistoryResponse:
    today = datetime.utcnow().date()
    start_date = today - timedelta(days=days - 1)

    result = session.execute(
        select(
            func.date(MCPLLMAxisScore.scored_at).label("date"),
            MCPLLMAxisScore.axis_label,
            MCPLLMAxisScore.p_top,
            MCPLLMAxisScore.p_critical,
            MCPLLMAxisScore.p_danger,
        )
        .where(MCPLLMAxisScore.server_id == server_id)
        .where(func.date(MCPLLMAxisScore.scored_at) >= start_date)
        .order_by(func.date(MCPLLMAxisScore.scored_at), MCPLLMAxisScore.axis_label)
    )

    rows = result.all()

    series_dict: dict[str, dict[str, Any]] = {}
    for row in rows:
        date_str = str(row.date)
        if date_str not in series_dict:
            series_dict[date_str] = {"date": date_str, "scores": {}}
        series_dict[date_str]["scores"][row.axis_label] = {
            "label": row.axis_label,
            "p_top": row.p_top,
            "p_critical": row.p_critical,
            "p_danger": row.p_danger,
        }

    series = [series_dict[date_str] for date_str in sorted(series_dict.keys())]

    return ScoreHistoryResponse(server_id=server_id, days=days, series=series)


if __name__ == "__main__":
    import sys
    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import sessionmaker

    from app.models import MCPServerRegistry, MCPLLMAxisScore

    engine = create_engine("sqlite:///:memory:", echo=False)
    Base.metadata.create_all(bind=engine)

    SessionLocal = sessionmaker(bind=engine)
    session = SessionLocal()

    session.execute(
        text("""
            CREATE TABLE IF NOT EXISTS mcp_server_registry (
                id TEXT PRIMARY KEY,
                server_id TEXT NOT NULL,
                name TEXT,
                created_at TIMESTAMP
            )
        """)
    )

    session.execute(
        text("""
            CREATE TABLE IF NOT EXISTS mcp_llm_axis_scores (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                server_id TEXT NOT NULL,
                scored_at TIMESTAMP NOT NULL,
                axis_label TEXT NOT NULL,
                p_top REAL,
                p_critical REAL,
                p_danger REAL
            )
        """)
    )

    session.execute(
        text("INSERT INTO mcp_server_registry (id, server_id, name) VALUES (:id, :server_id, :name)"),
        [{"id": "s1", "server_id": "server-1", "name": "Server One"}],
    )
    session.execute(
        text("INSERT INTO mcp_server_registry (id, server_id, name) VALUES (:id, :server_id, :name)"),
        [{"id": "s2", "server_id": "server-2", "name": "Server Two"}],
    )

    today = datetime.utcnow()
    scores_data = []

    for day_offset in range(3):
        scored_at = today - timedelta(days=2 - day_offset)
        for axis in ["security", "reliability", "performance"]:
            p_top = 0.8 if day_offset == 0 else (0.6 if day_offset == 1 else 0.4)
            scores_data.append({
                "server_id": "server-1",
                "scored_at": scored_at.isoformat(),
                "axis_label": axis,
                "p_top": p_top,
                "p_critical": 1 - p_top,
                "p_danger": 0.1,
            })
            scores_data.append({
                "server_id": "server-2",
                "scored_at": scored_at.isoformat(),
                "axis_label": axis,
                "p_top": 0.7,
                "p_critical": 0.2,
                "p_danger": 0.1,
            })

    session.execute(
        text("""
            INSERT INTO mcp_llm_axis_scores 
            (server_id, scored_at, axis_label, p_top, p_critical, p_danger)
            VALUES (:server_id, :scored_at, :axis_label, :p_top, :p_critical, :p_danger)
        """),
        scores_data,
    )
    session.commit()

    app.dependency_overrides[get_session] = lambda: session

    from fastapi.testclient import TestClient
    from main import app

    client = TestClient(app)

    response = client.get("/api/servers/server-1/score-history?days=3")
    assert response.status_code == 200, f"Expected 200, got {response.status_code}"

    data = response.json()
    assert len(data["series"]) == 3, f"Expected 3 days, got {len(data['series'])}"

    has_p_top = False
    for day in data["series"]:
        for axis_name, axis_data in day["scores"].items():
            if axis_data.get("p_top") is not None and axis_data["p_top"] > 0:
                has_p_top = True
                break
        if has_p_top:
            break

    assert has_p_top, "Expected at least one axis with p_top value"

    print("PASS")