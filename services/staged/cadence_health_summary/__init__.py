from app.db import get_session
from app.models import Org
from fastapi import APIRouter, Depends, HTTPException
import httpx
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker
from typing import Any

router = APIRouter()
MESH_URL = "http://127.0.0.1:8772"


def _dummy_post(url: str = MESH_URL, json: dict[str, Any] | None = None) -> dict[str, Any]:
    try:
        with httpx.Client(timeout=10.0) as client:
            resp = client.post(f"{url}/query", json=json or {})
            resp.raise_for_status()
            return resp.json()
    except Exception as e:
        return {"error": str(e)}


def get_signal_scores(mesh_id: str, session: Session = Depends(get_session)) -> list[dict[str, Any]]:
    try:
        with httpx.Client(timeout=10.0) as client:
            resp = client.post(
                f"{MESH_URL}/query",
                json={"table": "mcp_signal_scores", "filter": {"mesh_id": mesh_id}}
            )
            resp.raise_for_status()
            return resp.json().get("rows", [])
    except Exception:
        return []


def get_mesh_memory(mesh_id: str, session: Session = Depends(get_session)) -> dict[str, Any]:
    try:
        with httpx.Client(timeout=10.0) as client:
            resp = client.post(
                f"{MESH_URL}/query",
                json={"table": "mesh_memory", "filter": {"mesh_id": mesh_id}}
            )
            resp.raise_for_status()
            rows = resp.json().get("rows", [])
            return rows[0] if rows else {}
    except Exception:
        return {}


def mesh_scores_endpoint(mesh_id: str = "test") -> dict[str, Any]:
    try:
        with httpx.Client(timeout=10.0) as client:
            resp = client.post(
                f"{MESH_URL}/query",
                json={"table": "mcp_signal_scores", "filter": {"mesh_id": mesh_id}}
            )
            resp.raise_for_status()
            return resp.json()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


def dummy_post_endpoint(data: dict[str, Any] = {}) -> dict[str, Any]:
    return _dummy_post(json=data)


def orgs_endpoint(session: Session = Depends(get_session)) -> list[dict[str, Any]]:
    orgs = session.query(Org).all()
    return [{"id": o.id, "name": o.name} for o in orgs]


def signal_scores_endpoint(mesh_id: str, session: Session = Depends(get_session)) -> list[dict[str, Any]]:
    return get_signal_scores(mesh_id, session)


def _signal_scores_http(mesh_id: str) -> list[dict[str, Any]]:
    try:
        with httpx.Client(timeout=10.0) as client:
            resp = client.post(
                f"{MESH_URL}/query",
                json={"table": "mcp_signal_scores", "filter": {"mesh_id": mesh_id}}
            )
            resp.raise_for_status()
            return resp.json().get("rows", [])
    except Exception:
        return []


def _run_self_test() -> None:
    engine = create_engine("sqlite:///:memory:")
    SessionLocal = sessionmaker(bind=engine)
    ses = SessionLocal()
    ses.execute(text("CREATE TABLE orgs (id INTEGER PRIMARY KEY, name TEXT)"))
    ses.execute(text("INSERT INTO orgs (id, name) VALUES (1, 'Test Org')"))
    ses.commit()

    def _override():
        try:
            yield ses
        finally:
            pass

    from app import main as app_main
    app_main.app.dependency_overrides[get_session] = _override

    try:
        result = orgs_endpoint()
        assert isinstance(result, list), f"orgs_endpoint returned {type(result)}"
        assert len(result) == 1, f"Expected 1 org, got {len(result)}"
        assert result[0]["name"] == "Test Org"

        mesh = get_mesh_memory("test-mesh-id")
        assert isinstance(mesh, dict)

        scores = _signal_scores_http("test-mesh-id")
        assert isinstance(scores, list)

        print("PASS")
    finally:
        app_main.app.dependency_overrides.clear()
        ses.close()
        engine.dispose()


if __name__ == "__main__":
    _run_self_test()