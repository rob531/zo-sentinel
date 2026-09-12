from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from typing import Optional
from datetime import datetime, timedelta
import requests
import statistics

from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry

router = APIRouter()

SIGNAL_STORE_URL = "http://127.0.0.1:8772/query"


def write_signal(server_id: str, signal_type: str, payload: dict) -> dict:
    payload["server_id"] = server_id
    payload["signal_type"] = signal_type
    payload["created_at"] = datetime.utcnow().isoformat()
    try:
        resp = requests.post(
            SIGNAL_STORE_URL,
            json={"action": "write", "table": "mcp_signal_scores", "data": payload},
            timeout=5,
        )
        return resp.json()
    except Exception as e:
        return {"error": str(e)}


@router.get("/axis-scores")
def get_axis_scores(
    server_id: Optional[str] = None,
    axis_name: Optional[str] = None,
    limit: int = 100,
    session: Session = Depends(get_session),
):
    q = session.query(McpLlmAxisScore, McpServerRegistry).join(
        McpServerRegistry, McpLlmAxisScore.server_id == McpServerRegistry.server_id
    )
    if server_id:
        q = q.filter(McpLlmAxisScore.server_id == server_id)
    if axis_name:
        q = q.filter(McpLlmAxisScore.axis_name == axis_name)
    q = q.order_by(McpLlmAxisScore.scored_at.desc()).limit(limit)
    results = []
    for score, server in q.all():
        results.append(
            {
                "id": score.id,
                "server_id": score.server_id,
                "server_name": server.name,
                "axis_name": score.axis_name,
                "label": score.label,
                "label_index": score.label_index,
                "p_top": score.p_top,
                "p_critical": score.p_critical,
                "p_danger": score.p_danger,
                "scored_at": score.scored_at.isoformat() if score.scored_at else None,
            }
        )
    return {"axis_scores": results}


@router.get("/axis-volatility")
def get_axis_volatility(
    server_id: str,
    axis_name: str,
    window_hours: int = 24,
    session: Session = Depends(get_session),
):
    cutoff = datetime.utcnow() - timedelta(hours=window_hours)
    q = (
        session.query(McpLlmAxisScore)
        .filter(
            McpLlmAxisScore.server_id == server_id,
            McpLlmAxisScore.axis_name == axis_name,
            McpLlmAxisScore.scored_at >= cutoff,
        )
        .order_by(McpLlmAxisScore.scored_at)
    )
    scores = q.all()
    if not scores:
        return {"server_id": server_id, "axis_name": axis_name, "volatility": None, "score_count": 0}
    p_top_vals = [s.p_top for s in scores if s.p_top is not None]
    if len(p_top_vals) < 2:
        return {"server_id": server_id, "axis_name": axis_name, "volatility": 0.0, "score_count": len(p_top_vals)}
    volatility = statistics.stdev(p_top_vals) if len(p_top_vals) > 1 else 0.0
    return {"server_id": server_id, "axis_name": axis_name, "volatility": volatility, "score_count": len(p_top_vals)}


@router.post("/compute-volatility")
def compute_volatility(
    server_id: str,
    axis_name: str,
    window_hours: int = 24,
    session: Session = Depends(get_session),
):
    result = get_axis_volatility(server_id, axis_name, window_hours, session)
    volatility = result.get("volatility")
    if volatility is None:
        return {"status": "no_data", "server_id": server_id, "axis_name": axis_name}
    write_signal(
        server_id,
        "axis_volatility",
        {"axis_name": axis_name, "volatility": volatility, "window_hours": window_hours},
    )
    return {"status": "written", "server_id": server_id, "axis_name": axis_name, "volatility": volatility}


@router.get("/servers")
def list_servers(session: Session = Depends(get_session)):
    servers = session.query(McpServerRegistry).all()
    return {"servers": [{"server_id": s.server_id, "name": s.name, "risk_tier": s.risk_tier} for s in servers]}


@router.get("/health")
def health():
    return {"status": "ok", "service": "server_axis_volatility_scoring_consumer"}


if __name__ == "__main__":
    from fastapi import FastAPI
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    import threading

    engine = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    from app.models import Base
    Base.metadata.create_all(bind=engine)
    TestingSession = sessionmaker(bind=engine)

    def override_get_session():
        db = TestingSession()
        try:
            yield db
        finally:
            db.close()

    app = FastAPI()
    app.include_router(router)

    def run_and_test():
        import uvicorn

        def start():
            uvicorn.run(app, host="127.0.0.1", port=8783, log_level="error")

        t = threading.Thread(target=start, daemon=True)
        t.start()
        import time

        time.sleep(2)

        import requests

        try:
            resp = requests.get("http://127.0.0.1:8783/health", timeout=5)
            if resp.status_code == 200 and resp.json().get("status") == "ok":
                print("PASS")
            else:
                print("FAIL")
        except Exception as e:
            print(f"FAIL: {e}")

    app.dependency_overrides[get_session] = override_get_session
    run_and_test()