# services/staged/app_router_registry_api/logic.py
from __future__ import annotations

import importlib
import json
from datetime import datetime
from typing import List, Optional, Dict, Any

import requests
from fastapi import Depends, FastAPI, APIRouter
from pydantic import BaseModel, Field
from starlette.routing import Route

# ----------------------------------------------------------------------
# Data access
# ----------------------------------------------------------------------
from app.db import get_session  # noqa: F401  (dependency injection placeholder)


# ----------------------------------------------------------------------
# Pydantic models
# ----------------------------------------------------------------------
class RouterInfo(BaseModel):
    name: str = Field(..., description="Endpoint name")
    prefix: str = Field(..., description="Mounted prefix")
    tags: List[str] = Field(default_factory=list, description="Router tags")
    methods: List[str] = Field(default_factory=list, description="HTTP methods")
    health_status: Optional[str] = Field(
        None, description="Health status of the backing daemon"
    )
    last_heartbeat: Optional[datetime] = Field(
        None, description="Timestamp of the last heartbeat"
    )


class RoutersResponse(BaseModel):
    routers: List[RouterInfo] = Field(default_factory=list)


# ----------------------------------------------------------------------
# Helper: query service health via the write‑service bus
# ----------------------------------------------------------------------
_WRITE_SERVICE_URL = "http://127.0.0.1:8772/query"


def _query_service_health() -> Dict[str, Dict[str, Any]]:
    """
    Returns a mapping ``service_name -> {health_status, last_heartbeat}``.
    The write‑service is expected to understand a simple SELECT query.
    """
    sql = "SELECT service_name, health_status, last_heartbeat FROM service_health"
    try:
        resp = requests.post(_WRITE_SERVICE_URL, json={"sql": sql})
        resp.raise_for_status()
        payload = resp.json()
        rows = payload.get("rows", [])
    except Exception:  # pragma: no cover
        rows = []

    health_map: Dict[str, Dict[str, Any]] = {}
    for row in rows:
        # row may be a dict or a list/tuple – normalise to dict
        if isinstance(row, (list, tuple)):
            # assume order matches the SELECT
            service_name, health_status, last_heartbeat = row
        elif isinstance(row, dict):
            service_name = row.get("service_name")
            health_status = row.get("health_status")
            last_heartbeat = row.get("last_heartbeat")
        else:
            continue

        if isinstance(last_heartbeat, str):
            try:
                last_heartbeat = datetime.fromisoformat(last_heartbeat)
            except Exception:
                last_heartbeat = None

        health_map[service_name] = {
            "health_status": health_status,
            "last_heartbeat": last_heartbeat,
        }
    return health_map


# ----------------------------------------------------------------------
# Core logic: enumerate mounted routers and enrich with health data
# ----------------------------------------------------------------------
def list_routers(app: FastAPI) -> RoutersResponse:
    """
    Introspects ``app`` for all mounted ``APIRoute`` objects,
    extracts their metadata and enriches each entry with health
    information obtained from the service health bus.
    """
    health_map = _query_service_health()

    router_infos: List[RouterInfo] = []

    for route in app.routes:
        # We only care about APIRoute (i.e. HTTP endpoints)
        if not isinstance(route, Route):
            continue

        # ``route`` may be an ``APIRoute`` (has ``methods`` and ``name``)
        name = getattr(route, "name", None) or getattr(route, "endpoint", None)
        if not name:
            continue

        # Normalise name to string
        name = str(name)

        # Path may contain parameters – keep as‑is
        prefix = getattr(route, "path", "")

        # Tags are stored on the endpoint function (FastAPI copies them)
        tags: List[str] = getattr(route, "tags", []) or []

        # Methods is a set; we drop HEAD/OPTIONS which FastAPI adds automatically
        methods = sorted(
            m for m in getattr(route, "methods", []) if m not in {"HEAD", "OPTIONS"}
        )

        # Derive a service identifier – convention: first path component after '/'
        service_key = prefix.split("/")[1] if "/" in prefix else prefix

        health_entry = health_map.get(service_key, {})
        router_infos.append(
            RouterInfo(
                name=name,
                prefix=prefix,
                tags=tags,
                methods=methods,
                health_status=health_entry.get("health_status"),
                last_heartbeat=health_entry.get("last_heartbeat"),
            )
        )

    return RoutersResponse(routers=router_infos)


# ----------------------------------------------------------------------
# FastAPI router exposing the endpoint (used by the service's router.py)
# ----------------------------------------------------------------------
router = APIRouter()


@router.get(
    "/routers",
    response_model=RoutersResponse,
    tags=["router-registry"],
    summary="List all mounted FastAPI routers with health status",
)
def get_routers(fastapi_app: FastAPI = Depends(lambda: importlib.import_module("app.main").app)):
    """
    Endpoint that returns the list of routers discovered in the main
    FastAPI application together with health information.
    """
    return list_routers(fastapi_app)


# ----------------------------------------------------------------------
# Self‑test
# ----------------------------------------------------------------------
if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    # ------------------------------------------------------------------
    # Build a minimal test application
    # ------------------------------------------------------------------
    test_app = FastAPI()
    # mount the endpoint defined above
    test_app.include_router(router, prefix="/api")

    # create two dummy routers to be discovered
    dummy1 = APIRouter(prefix="/dummy1", tags=["dummy"])
    dummy2 = APIRouter(prefix="/dummy2", tags=["dummy"])

    @dummy1.get("/ping")
    def ping1():
        return {"msg": "pong1"}

    @dummy2.get("/ping")
    def ping2():
        return {"msg": "pong2"}

    test_app.include_router(dummy1)
    test_app.include_router(dummy2)

    # ------------------------------------------------------------------
    # Mock the write‑service health query
    # ------------------------------------------------------------------
    class _MockResponse:
        def __init__(self, data: dict):
            self._data = data

        def raise_for_status(self):
            pass

        def json(self):
            return self._data

    def _mock_post(url, json=None, **kwargs):
        # Return health rows for the two dummy services
        mock_rows = [
            {
                "service_name": "dummy1",
                "health_status": "healthy",
                "last_heartbeat": datetime.utcnow().isoformat(),
            },
            {
                "service_name": "dummy2",
                "health_status": "degraded",
                "last_heartbeat": datetime.utcnow().isoformat(),
            },
        ]
        return _MockResponse({"rows": mock_rows})

    # Patch requests.post only for the duration of the test
    original_post = requests.post
    requests.post = _mock_post  # type: ignore

    try:
        client = TestClient(test_app)
        resp = client.get("/api/routers")
        assert resp.status_code == 200, f"Unexpected status {resp.status_code}"
        data = resp.json()
        assert "routers" in data, "Missing 'routers' key"
        assert isinstance(data["routers"], list), "'routers' is not a list"
        assert len(data["routers"]) >= 2, "Expected at least two router entries"
        print("PASS")
    finally:
        # Restore original requests.post
        requests.post = original_post