"""Acceptance self-test for the cadence cost attribution service."""
from __future__ import annotations

import inspect
import sys
from datetime import datetime, timezone


def run() -> bool:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.db import get_session
    from app.models import Base, CadenceJobRun, McpServerRegistry

    from .router import router

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    TestingSession = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    session = TestingSession()
    session.add(McpServerRegistry(server_id="srv1", name="A", risk_tier="HIGH"))
    session.add(McpServerRegistry(server_id="srv2", name="B", risk_tier="HIGH"))
    session.add(McpServerRegistry(server_id="srv3", name="C", risk_tier="LOW"))
    session.commit()
    assert session.query(CadenceJobRun).count() == 0
    session.close()

    def _override():
        db = TestingSession()
        try:
            yield db
        finally:
            db.close()

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = _override

    client = TestClient(app)
    candidates = []
    fallback = []

    for route in app.routes:
        methods = getattr(route, "methods", set())
        path = getattr(route, "path", "")
        if "GET" not in methods:
            continue
        item = (route, path)
        fallback.append(item)
        lowered = path.lower()
        if any(term in lowered for term in ("cadence", "cost", "attribution", "throughput")):
            candidates.append(item)

    routes = candidates or fallback
    assert routes, "the cadence cost attribution router exposes no GET endpoint"

    failures = []
    successful_body = None
    for route, path in routes:
        params = {}
        for parameter in getattr(getattr(route, "dependant", None), "query_params", []):
            if parameter.default is not inspect.Parameter.empty:
                continue
            annotation = parameter.annotation
            name = parameter.name.lower()
            if isinstance(annotation, bool):
                value = True
            elif isinstance(annotation, int):
                value = 10 if "limit" in name else 1
            elif isinstance(annotation, float):
                value = 1.0
            elif "limit" in name or "offset" in name or "count" in name:
                value = 10 if "limit" in name else 0
            elif "status" in name:
                value = "success"
            elif "job" in name:
                value = "job1"
            elif "window" in name:
                value = "24h"
            elif "since" in name or "start" in name or "end" in name:
                value = datetime.now(timezone.utc).isoformat()
            elif "sort" in name or "order" in name:
                value = "desc"
            elif annotation is str or annotation is inspect.Parameter.empty:
                value = "all"
            else:
                value = "all"
            params[parameter.name] = value

        response = client.get(path, params=params)
        if response.status_code == 200:
            successful_body = response.json()
            break
        failures.append("%s: %s %s" % (path, response.status_code, response.text))

    assert successful_body is not None, "; ".join(failures) or "no successful response"
    assert isinstance(successful_body, (dict, list)), successful_body
    if isinstance(successful_body, dict):
        assert "error" not in successful_body, successful_body

    return True


if __name__ == "__main__":
    try:
        run()
    except Exception as exc:  # noqa: BLE001 -- acceptance failures must be loud
        print("FAIL: %r" % (exc,))
        sys.exit(1)
    print("PASS")
    sys.exit(0)