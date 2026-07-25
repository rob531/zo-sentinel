from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select

from app.db import get_session
from app.models import MCPLLMAxisScore

router = APIRouter()

SEVEN_AXES = [
    "accuracy",
    "coherence",
    "relevance",
    "hallucination_free",
    "toxicity_free",
    "bias_minimized",
    "safety",
]


@router.get("/servers/{server_id}/axis-scores")
def get_axis_score_summary(server_id: str) -> dict:
    with get_session() as session:
        stmt = select(MCPLLMAxisScore).where(
            MCPLLMAxisScore.server_id == server_id,
            MCPLLMAxisScore.axis_name.in_(SEVEN_AXES),
        )
        results = session.execute(stmt).scalars().all()

    axis_scores = {}
    for row in results:
        axis_scores[row.axis_name] = {
            "label": row.label,
            "p_top": row.p_top,
            "p_critical": row.p_critical,
            "p_danger": row.p_danger,
        }

    return {"server_id": server_id, "axis_scores": axis_scores}


if __name__ == "__main__":
    import sqlite3
    from app import main as app_main

    app = app_main

    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.execute(
        "CREATE TABLE mcp_llm_axis_scores (id INTEGER PRIMARY KEY, server_id TEXT NOT NULL, axis_name TEXT NOT NULL, label TEXT, p_top REAL, p_critical REAL, p_danger REAL)"
    )
    conn.execute(
        "INSERT INTO mcp_llm_axis_scores (server_id, axis_name, label, p_top, p_critical, p_danger) VALUES ('test-server', 'accuracy', 'Accuracy', 0.7, 0.1, 0.2)"
    )
    conn.execute(
        "INSERT INTO mcp_llm_axis_scores (server_id, axis_name, label, p_top, p_critical, p_danger) VALUES ('test-server', 'coherence', 'Coherence', 0.85, 0.05, 0.1)"
    )
    conn.execute(
        "INSERT INTO mcp_llm_axis_scores (server_id, axis_name, label, p_top, p_critical, p_danger) VALUES ('test-server', 'hallucination_free', 'Hallucination-Free', 0.65, 0.15, 0.2)"
    )
    conn.commit()

    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    engine.execute(
        "CREATE TABLE mcp_llm_axis_scores (id INTEGER PRIMARY KEY, server_id TEXT NOT NULL, axis_name TEXT NOT NULL, label TEXT, p_top REAL, p_critical REAL, p_danger REAL)"
    )
    engine.execute(
        "INSERT INTO mcp_llm_axis_scores (server_id, axis_name, label, p_top, p_critical, p_danger) VALUES ('test-server', 'accuracy', 'Accuracy', 0.7, 0.1, 0.2)"
    )
    engine.execute(
        "INSERT INTO mcp_llm_axis_scores (server_id, axis_name, label, p_top, p_critical, p_danger) VALUES ('test-server', 'coherence', 'Coherence', 0.85, 0.05, 0.1)"
    )
    engine.execute(
        "INSERT INTO mcp_llm_axis_scores (server_id, axis_name, label, p_top, p_critical, p_danger) VALUES ('test-server', 'hallucination_free', 'Hallucination-Free', 0.65, 0.15, 0.2)"
    )
    TestSession = sessionmaker(bind=engine)

    original_get_session = get_session

    def mock_get_session():
        yield TestSession()

    app.dependency_overrides[get_session] = mock_get_session

    result = get_axis_score_summary("test-server")

    assert result["server_id"] == "test-server"
    assert "axis_scores" in result
    assert set(result["axis_scores"].keys()) == {"accuracy", "coherence", "hallucination_free"}

    for axis_name in result["axis_scores"]:
        axis = result["axis_scores"][axis_name]
        assert "label" in axis
        assert "p_top" in axis
        assert "p_critical" in axis
        assert "p_danger" in axis
        assert isinstance(axis["p_top"], (int, float))
        assert isinstance(axis["p_critical"], (int, float))
        assert isinstance(axis["p_danger"], (int, float))

    app.dependency_overrides.clear()

    print("PASS")