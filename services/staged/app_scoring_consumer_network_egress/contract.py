from datetime import datetime, timezone
from typing import Any

import requests
from fastapi import APIRouter, Depends, FastAPI
from pydantic import BaseModel
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool
from starlette.testclient import TestClient

from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry

router = APIRouter()


class ScoreInput(BaseModel):
    server_id: str


class ScoreOutput(BaseModel):
    score: float
    confidence: float
    evidence: dict


WRITE_SERVICE_URL = "http://127.0.0.1:8772"
SERVICE_NAME = "app_scoring_consumer_network_egress"
HEALTH_INTERVAL = 60


def write_service_post(path: str, json: dict) -> dict:
    resp = requests.post(f"{WRITE_SERVICE_URL}{path}", json=json, timeout=10)
    resp.raise_for_status()
    return resp.json()


def query_axis_scores(server_ids: list[str]) -> list[dict]:
    result = write_service_post("/query", {
        "table": "mcp_llm_axis_scores",
        "filters": [
            {"column": "axis_name", "op": "eq", "value": "network_egress"},
            {"column": "server_id", "op": "in", "value": server_ids}
        ]
    })
    return result.get("rows", [])


def query_servers(server_ids: list[str]) -> list[dict]:
    result = write_service_post("/query", {
        "table": "mcp_server_registry",
        "filters": [{"column": "server_id", "op": "in", "value": server_ids}]
    })
    return result.get("rows", [])


def compute_score(server_id: str) -> dict:
    axis_rows = query_axis_scores([server_id])
    server_rows = query_servers([server_id])

    axis = axis_rows[0] if axis_rows else {}
    server = server_rows[0] if server_rows else {}

    trust_score = server.get("trust_score", 0.5)
    registry_source = server.get("registry_source", "unknown")
    url = server.get("url", "")

    p_top = axis.get("p_top", 0.25)
    p_critical = axis.get("p_critical", 0.15)
    p_danger = axis.get("p_danger", 0.20)
    label = axis.get("label", "medium")
    label_index = axis.get("label_index", 2)

    risk_indicators = []
    if p_top > 0.5:
        risk_indicators.append("high_top_probability")
    if p_critical > 0.3:
        risk_indicators.append("critical_exposure")
    if "url" in str(url).lower() or url:
        risk_indicators.append("egress_url_detected")

    if label_index >= 3:
        signal_strength = 0.8
    elif label_index == 2:
        signal_strength = 0.5
    else:
        signal_strength = 0.2

    base_score = (1 - trust_score) * 50 + (1 - label_index / 4) * 30 + signal_strength * 20
    score = max(0, min(100, base_score))

    confidence = min(1.0, 0.4 + signal_strength * 0.4 + trust_score * 0.2)

    rationale_parts = [f"trust={trust_score:.2f}", f"label={label}", f"signals={len(risk_indicators)}"]
    if risk_indicators:
        rationale_parts.append(f"indicators={','.join(risk_indicators)}")
    rationale = "; ".join(rationale_parts)

    evidence = {
        "registry_source": registry_source,
        "trust_score": trust_score,
        "signal_strength": signal_strength,
        "rationale": rationale
    }

    return {
        "score": round(score, 2),
        "confidence": round(confidence, 2),
        "evidence": evidence
    }


def write_score(server_id: str, score_data: dict) -> None:
    row = {
        "server_id": server_id,
        "axis_name": "network_egress",
        "decision_rule_version": "r2",
        "scored_at": datetime.now(timezone.utc).isoformat(),
        "score": score_data["score"],
        "confidence": score_data["confidence"],
        "evidence": score_data["evidence"]
    }
    write_service_post("/write", {
        "table": "mcp_llm_axis_scores",
        "rows": [row]
    })


def send_heartbeat() -> None:
    try:
        write_service_post("/write", {
            "table": "service_health",
            "rows": [{
                "service": SERVICE_NAME,
                "status": "running",
                "timestamp": datetime.now(timezone.utc).isoformat()
            }]
        })
    except Exception:
        pass


def run():
    send_heartbeat()
    while True:
        try:
            result = write_service_post("/query", {
                "table": "mcp_llm_axis_scores",
                "filters": [{"column": "axis_name", "op": "eq", "value": "network_egress"}],
                "limit": 100
            })
            server_ids = list({row["server_id"] for row in result.get("rows", [])})
            for sid in server_ids:
                try:
                    score_data = compute_score(sid)
                    write_score(sid, score_data)
                except Exception:
                    pass
        except Exception:
            pass
        send_heartbeat()
        import time
        time.sleep(HEALTH_INTERVAL)


@router.post("/score")
def score_server(input_data: ScoreInput, session=Depends(get_session)):
    score_data = compute_score(input_data.server_id)
    write_score(input_data.server_id, score_data)
    return ScoreOutput(**score_data)


def create_app() -> FastAPI:
    app = FastAPI(title=SERVICE_NAME)
    app.include_router(router)
    return app


if __name__ == "__main__":
    import json
    import threading
    import time
    from queue import Queue

    in_memory_rows: dict[str, list[dict]] = {
        "mcp_llm_axis_scores": [],
        "mcp_server_registry": [],
        "service_health": []
    }
    request_queue: Queue = Queue()

    def mock_write_service():
        while True:
            req = request_queue.get()
            if req is None:
                break
            path = req["path"]
            json_data = req["json"]
            if path == "/query":
                table = json_data.get("table")
                rows = in_memory_rows.get(table, [])
                filters = json_data.get("filters", [])
                for f in filters:
                    col = f["column"]
                    op = f["op"]
                    val = f["value"]
                    if op == "eq":
                        rows = [r for r in rows if r.get(col) == val]
                    elif op == "in":
                        rows = [r for r in rows if r.get(col) in val]
                print(json.dumps({"rows": rows}))
            elif path == "/write":
                table = json_data.get("table")
                rows = json_data.get("rows", [])
                if table not in in_memory_rows:
                    in_memory_rows[table] = []
                in_memory_rows[table].extend(rows)
                print(json.dumps({"status": "ok"}))
            request_queue.task_done()

    mock_thread = threading.Thread(target=mock_write_service, daemon=True)
    mock_thread.start()

    original_post = requests.post

    def mock_post(url, json=None, timeout=None):
        path = url.replace("http://127.0.0.1:8772", "")
        queue_response = Queue()
        request_queue.put({"path": path, "json": json, "response": queue_response})

        def wait_response():
            result = queue_response.get()
            class MockResp:
                def __init__(self, data):
                    self._data = data
                def json(self):
                    return json.loads(self._data)
                def raise_for_status(self):
                    pass
            return MockResp(result)

        class AsyncMockPost:
            def __enter__(self):
                return mock_post
            def __exit__(self, *args):
                pass
        return mock_post(url, json, timeout)

    in_memory_rows["mcp_llm_axis_scores"] = [
        {
            "server_id": "server_high",
            "axis_name": "network_egress",
            "p_top": 0.6,
            "p_critical": 0.4,
            "p_danger": 0.3,
            "label": "high",
            "label_index": 3
        },
        {
            "server_id": "server_low",
            "axis_name": "network_egress",
            "p_top": 0.1,
            "p_critical": 0.05,
            "p_danger": 0.1,
            "label": "low",
            "label_index": 0
        }
    ]
    in_memory_rows["mcp_server_registry"] = [
        {
            "server_id": "server_high",
            "trust_score": 0.2,
            "registry_source": "npm",
            "url": "https://example.com/egress"
        },
        {
            "server_id": "server_low",
            "trust_score": 0.9,
            "registry_source": "verified",
            "url": ""
        }
    ]

    engine = create_engine("sqlite:///:memory:", poolclass=StaticPool, connect_args={"check_same_thread": False})
    from app.models import Base
    Base.metadata.create_all(engine)

    from sqlalchemy.orm import sessionmaker
    Session = sessionmaker(bind=engine)

    def override_get_session():
        session = Session()
        try:
            yield session
        finally:
            session.close()

    test_app = create_app()
    test_app.dependency_overrides[get_session] = override_get_session

    from unittest.mock import patch
    with patch("requests.post") as mock_post:
        def side_effect(url, json=None, timeout=None):
            path = url.replace("http://127.0.0.1:8772", "")
            resp = type("Response", (), {"json": lambda s: json.loads(in_memory_rows.get("last_result", "{}")), "raise_for_status": lambda s: None})()
            if path == "/query":
                table = json.get("table")
                rows = in_memory_rows.get(table, [])
                filters = json.get("filters", [])
                for f in filters:
                    col = f["column"]
                    op = f["op"]
                    val = f["value"]
                    if op == "eq":
                        rows = [r for r in rows if r.get(col) == val]
                    elif op == "in":
                        rows = [r for r in rows if r.get(col) in val]
                resp.json = lambda: {"rows": rows}
            elif path == "/write":
                table = json.get("table")
                rows = json.get("rows", [])
                if table not in in_memory_rows:
                    in_memory_rows[table] = []
                in_memory_rows[table].extend(rows)
                resp.json = lambda: {"status": "ok"}
            return resp

        mock_post.side_effect = side_effect

        score_high = compute_score("server_high")
        score_low = compute_score("server_low")

        assert score_high["score"] > score_low["score"], f"Expected high > low: {score_high['score']} vs {score_low['score']}"
        assert 0.0 <= score_high["confidence"] <= 1.0, f"Confidence out of range: {score_high['confidence']}"
        assert 0.0 <= score_low["confidence"] <= 1.0, f"Confidence out of range: {score_low['confidence']}"
        assert "registry_source" in score_high["evidence"], "Missing registry_source in evidence"
        assert "rationale" in score_high["evidence"], "Missing rationale in evidence"
        assert "registry_source" in score_low["evidence"], "Missing registry_source in evidence"
        assert "rationale" in score_low["evidence"], "Missing rationale in evidence"

    request_queue.put(None)
    print("PASS")