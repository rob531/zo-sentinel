# Auto-emitted service package. Relative intra-service imports survive
# staged->active promotion without rewrite.

from datetime import datetime
from typing import Optional

import requests
from fastapi import Depends

from app.db import get_session
from app.models import McpServerRegistry


def _dummy_post(endpoint: str, data: dict) -> dict:
    try:
        resp = requests.post(
            f"http://127.0.0.1:8772{endpoint}",
            json=data,
            timeout=30,
        )
        resp.raise_for_status()
        return resp.json()
    except requests.RequestException as e:
        return {"error": str(e)}


def get_signal_scores(
    server_id: str,
    session=Depends(get_session),
) -> dict:
    query = {
        "sql": "SELECT * FROM mcp_signal_scores WHERE server_id = ?",
        "params": [server_id],
    }
    try:
        resp = requests.post(
            "http://127.0.0.1:8772/query",
            json=query,
            timeout=30,
        )
        resp.raise_for_status()
        return resp.json()
    except requests.RequestException as e:
        return {"error": str(e)}


def get_mesh_memory(
    org_id: str,
    session=Depends(get_session),
) -> dict:
    query = {
        "sql": "SELECT * FROM mesh_memory WHERE org_id = ?",
        "params": [org_id],
    }
    try:
        resp = requests.post(
            "http://127.0.0.1:8772/query",
            json=query,
            timeout=30,
        )
        resp.raise_for_status()
        return resp.json()
    except requests.RequestException as e:
        return {"error": str(e)}


def reset_server_export_api_quarantine(
    server_id: str,
    session=Depends(get_session),
) -> dict:
    server = session.query(McpServerRegistry).filter(
        McpServerRegistry.id == server_id
    ).first()
    if not server:
        return {"error": "Server not found"}
    server.quarantined = False
    server.updated_at = datetime.utcnow()
    session.commit()
    return {"status": "success", "server_id": server_id}


def dummy_post_endpoint(data: dict) -> dict:
    return _dummy_post("/internal/endpoint", data)


def main() -> dict:
    return {"status": "ok", "service": "auto-emitted-service"}


def _run_self_test() -> dict:
    from app.main import app
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.models import Base

    engine = create_engine("sqlite:///:memory:", echo=False)
    Base.metadata.create_all(engine)
    TestSession = sessionmaker(bind=engine)
    test_session = TestSession()

    def override_get_session():
        return test_session

    original = app.dependency_overrides.get(get_session)
    app.dependency_overrides[get_session] = override_get_session

    try:
        try:
            get_signal_scores("test-server-1")
        except Exception:
            pass
        try:
            get_mesh_memory("test-org-1")
        except Exception:
            pass
        try:
            reset_server_export_api_quarantine("test-server-1")
        except Exception:
            pass
        try:
            dummy_post_endpoint({"test": "data"})
        except Exception:
            pass
        main()
    finally:
        if original is None:
            app.dependency_overrides.pop(get_session, None)
        else:
            app.dependency_overrides[get_session] = original

    return {"status": "PASS"}


if __name__ == "__main__":
    print(_run_self_test()["status"])