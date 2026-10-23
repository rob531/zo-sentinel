# services/staged/risk_scorecard_export/contract.py
import sys
import io
from contextlib import redirect_stdout
from typing import List, Tuple

from fastapi import APIRouter, Depends, Response
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry

router = APIRouter()


def _build_csv(lines: List[str]) -> str:
    return "\n".join(lines) + "\n"


def get_server_scorecard_data(server_id: int, session: Session) -> Tuple[dict, List[dict]]:
    registry = session.query(McpServerRegistry).filter(McpServerRegistry.server_id == server_id).first()
    if not registry:
        return {}, []
    
    server_info = {
        "server_id": registry.server_id,
        "server_name": registry.name,
    }
    
    scores = (
        session.query(McpLlmAxisScore)
        .filter(McpLlmAxisScore.server_id == server_id)
        .order_by(McpLlmAxisScore.axis_name)
        .all()
    )
    
    rows = []
    for score in scores:
        rows.append({
            "server_id": score.server_id,
            "server_name": registry.name,
            "axis_name": score.axis_name,
            "label": score.label,
            "p_top": score.p_top,
            "p_critical": score.p_critical,
            "p_danger": score.p_danger,
            "escalated": score.escalated,
            "scored_at": score.scored_at.isoformat() if score.scored_at else "",
        })
    
    return server_info, rows


def get_composite_score(server_id: int, session: Session) -> float:
    overall = (
        session.query(McpLlmAxisScore)
        .filter(
            McpLlmAxisScore.server_id == server_id,
            McpLlmAxisScore.axis_name == "overall_risk",
        )
        .first()
    )
    if overall and overall.p_top is not None:
        return round(overall.p_top, 4)
    
    top_score = (
        session.query(McpLlmAxisScore)
        .filter(McpLlmAxisScore.server_id == server_id)
        .order_by(McpLlmAxisScore.p_top.desc())
        .first()
    )
    return round(top_score.p_top, 4) if top_score and top_score.p_top else 0.0


@router.get("/api/servers/{server_id}/scorecard")
def get_scorecard(server_id: int, session: Session = Depends(get_session)) -> Response:
    server_info, rows = get_server_scorecard_data(server_id, session)
    
    if not rows:
        return Response(content="No scorecard data found", status_code=404)
    
    lines = ["server_id,server_name,axis_name,label,p_top,p_critical,p_danger,escalated,scored_at"]
    
    for row in rows:
        probs_val = row["p_top"] + row["p_critical"] + row["p_danger"]
        lines.append(
            f"{row['server_id']},{row['server_name']},{row['axis_name']},{row['label']},"
            f"{row['p_top']},{row['p_critical']},{row['p_danger']},{row['escalated']},{row['scored_at']}"
        )
    
    composite = get_composite_score(server_id, session)
    lines.append(f"SUMMARY,,,,,,,composite_score={composite}")
    
    csv_content = _build_csv(lines)
    return Response(content=csv_content, media_type="text/csv")


def _run_self_test() -> bool:
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE mcp_server_registry (server_id INTEGER, name TEXT)"))
        conn.execute(text("CREATE TABLE mcp_llm_axis_scores (id INTEGER, server_id INTEGER, axis_name TEXT, label TEXT, p_top REAL, p_critical REAL, p_danger REAL, probs TEXT, escalated INTEGER, scored_at TEXT)"))
        
        for i in range(1, 3):
            conn.execute(text("INSERT INTO mcp_server_registry (server_id, name) VALUES (:s, :n)"), {"s": i, "n": f"test-server-{i}"})
            
            axis_names = ["overall_risk", "security_posture", "reliability", "performance", "compliance", "availability", "data_governance"]
            labels = ["critical", "warning", "good", "excellent", "fair", "stable", "compliant"]
            
            for j, (axis, label) in enumerate(zip(axis_names, labels)):
                p_top = 0.1 + (j * 0.1)
                p_crit = 0.05 + (j * 0.05)
                p_dang = 0.15 + (j * 0.08)
                esc = 1 if j == 0 else 0
                conn.execute(
                    text("INSERT INTO mcp_llm_axis_scores (id, server_id, axis_name, label, p_top, p_critical, p_danger, probs, escalated, scored_at) VALUES (:id, :s, :a, :l, :pt, :pc, :pd, :pr, :e, :sa)"),
                    {"id": i * 100 + j, "s": i, "a": axis, "l": label, "pt": p_top, "pc": p_crit, "pd": p_dang, "pr": "[0.5,0.3,0.2]", "e": esc, "sa": "2024-01-15T10:00:00"}
                )
    
    TestingSession = sessionmaker(bind=engine)
    
    def override_get_session():
        session = TestingSession()
        try:
            yield session
        finally:
            session.close()
    
    from fastapi import FastAPI
    from contract import router
    
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = override_get_session
    
    client = TestClient(app)
    response = client.get("/api/servers/1/scorecard")
    
    if response.status_code != 200:
        print(f"FAIL: status={response.status_code}")
        return False
    
    content_type = response.headers.get("content-type", "")
    if "csv" not in content_type.lower():
        print(f"FAIL: content-type={content_type}")
        return False
    
    content = response.text
    
    if "axis_name" not in content:
        print("FAIL: missing axis_name header")
        return False
    
    lines = [l for l in content.strip().split("\n") if l and not l.startswith("SUMMARY")]
    data_rows = [l for l in lines if not l.startswith("server_id,")]
    
    if len(data_rows) < 14:
        print(f"FAIL: expected >=14 data rows, got {len(data_rows)}")
        return False
    
    print("PASS")
    return True


if __name__ == "__main__":
    success = _run_self_test()
    sys.exit(0 if success else 1)