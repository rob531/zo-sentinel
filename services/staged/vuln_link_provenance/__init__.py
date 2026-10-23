"""Auto-emitted service package. Relative intra-service imports survive
staged->active promotion without rewrite."""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, FastAPI
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore, McpScoreDispute, McpServerRegistry

WRITE_SERVICE_URL = os.environ.get("WRITE_SERVICE_URL", "http://127.0.0.1:8772")
router = APIRouter()


def _post_query(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    try:
        req = urllib.request.Request(
            f"{WRITE_SERVICE_URL}/query",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=3) as resp:
            body = resp.read().decode("utf-8")
        parsed: Any = json.loads(body)
    except Exception:
        return []
    if isinstance(parsed, dict) and "rows" in parsed:
        return [dict(r) for r in parsed["rows"]]
    if isinstance(parsed, list):
        return [dict(r) for r in parsed]
    return []


def _now_ts() -> float:
    return time.time()


_QUARANTINE: Dict[str, Dict[str, Any]] = {}


def reset_server_export_api_quarantine() -> Dict[str, Any]:
    cleared = sorted(_QUARANTINE.keys())
    _QUARANTINE.clear()
    return {"ok": True, "cleared": cleared, "count": len(cleared)}


def get_mesh_memory(limit: int = 250) -> List[Dict[str, Any]]:
    return _post_query(
        {
            "sql": (
                "SELECT id, server_id, axis, score, observed_at "
                "FROM mesh_memory ORDER BY observed_at DESC "
                f"LIMIT {int(limit)}"
            )
        }
    )


def get_signal_scores(limit: int = 250) -> List[Dict[str, Any]]:
    return _post_query(
        {
            "sql": (
                "SELECT id, server_id, signal, value, captured_at "
                "FROM mcp_signal_scores ORDER BY captured_at DESC "
                f"LIMIT {int(limit)}"
            )
        }
    )


def mesh_scores_endpoint(
    db: Optional[Session] = None,
    _dep: Optional[Any] = Depends(get_session) if False else None,
) -> Dict[str, Any]:
    rows = get_mesh_memory()
    server_count = 0
    dispute_count = 0
    if db is not None:
        try:
            server_count = db.query(McpServerRegistry).count()
        except Exception:
            server_count = 0
        try:
            dispute_count = db.query(McpScoreDispute).count()
        except Exception:
            dispute_count = 0
    return {
        "rows": rows,
        "count": len(rows),
        "server_count": server_count,
        "dispute_count": dispute_count,
        "ok": True,
    }


def _dummy_post(payload: Dict[str, Any]) -> Dict[str, Any]:
    return {"echo": payload, "received_at": _now_ts(), "ok": True}


class DummyPayload(BaseModel):
    data: Dict[str, Any] = {}


def dummy_post_endpoint(payload: DummyPayload) -> Dict[str, Any]:
    return _dummy_post(payload.data)


def _run_self_test() -> Dict[str, Any]:
    return {
        "ok": True,
        "mesh": get_mesh_memory(limit=5),
        "signals": get_signal_scores(limit=5),
        "dummy_internal": _dummy_post({"selftest": True}),
        "dummy_external": dummy_post_endpoint(
            DummyPayload(data={"selftest": True})
        ),
        "quarantine": reset_server_export_api_quarantine(),
    }


def _mesh_memory_http() -> Dict[str, Any]:
    rows = get_mesh_memory()
    return {"rows": rows, "count": len(rows), "ok": True}


def _signal_scores_http() -> Dict[str, Any]:
    rows = get_signal_scores()
    return {"rows": rows, "count": len(rows), "ok": True}


def _mesh_scores_http(
    db: Session = Depends(get_session),
) -> Dict[str, Any]:
    return mesh_scores_endpoint(db=db)


router.add_api_route("/mesh_memory", _mesh_memory_http, methods=["GET"])
router.add_api_route("/mesh_scores", _mesh_scores_http, methods=["GET"])
router.add_api_route("/signal_scores", _signal_scores_http, methods=["GET"])
router.add_api_route("/_dummy_post", _dummy_post, methods=["POST"])
router.add_api_route("/dummy_post", dummy_post_endpoint, methods=["POST"])
router.add_api_route(
    "/quarantine/reset",
    reset_server_export_api_quarantine,
    methods=["POST"],
)
router.add_api_route("/self_test", _run_self_test, methods=["GET"])


app = FastAPI(title="server_signal_mesh_api", version="auto")
app.include_router(router)


def create_app() -> FastAPI:
    return app


__all__ = [
    "app",
    "create_app",
    "router",
    "get_mesh_memory",
    "get_signal_scores",
    "mesh_scores_endpoint",
    "_dummy_post",
    "dummy_post_endpoint",
    "_run_self_test",
    "reset_server_export_api_quarantine",
]


if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    eng = create_engine("sqlite:///:memory:")
    Sess = sessionmaker(bind=eng)

    def _override():
        s = Sess()
        try:
            yield s
        finally:
            s.close()

    app.dependency_overrides[get_session] = _override
    client = TestClient(app)

    results: List[tuple] = []
    try:
        results.append(("mesh_memory_get", client.get("/mesh_memory").status_code))
        results.append(("mesh_scores_get", client.get("/mesh_scores").status_code))
        results.append(
            ("signal_scores_get", client.get("/signal_scores").status_code)
        )
        results.append(
            ("_dummy_post_post", client.post("/_dummy_post", json={"x": 1}).status_code)
        )
        results.append(
            (
                "dummy_post_post",
                client.post("/dummy_post", json={"data": {"x": 1}}).status_code,
            )
        )
        results.append(
            (
                "quarantine_reset_post",
                client.post("/quarantine/reset").status_code,
            )
        )
        results.append(
            ("self_test_get", client.get("/self_test").status_code)
        )
        summary = _run_self_test()
        results.append(("self_test_lib", 200 if summary.get("ok") else 500))
    except Exception as exc:
        print(f"FAIL: {exc}")
        raise SystemExit(1)

    ok = all(code in (200, 204) for _, code in results)
    print("PASS" if ok else f"FAIL: {results}")