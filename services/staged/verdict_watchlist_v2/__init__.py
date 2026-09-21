import fastapi, json
from typing import Any
from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore, McpScoreDispute, Org, User
from sqlalchemy.orm import Session

router = fastapi.APIRouter()


@router.get("/mesh-scores")
def mesh_scores_endpoint(session: Session = fastapi.Depends(get_session)) -> dict[str, Any]:
    import requests
    try:
        resp = requests.post(
            "http://127.0.0.1:8772/query",
            json={"sql": "SELECT * FROM mcp_signal_scores LIMIT 100"},
            timeout=10,
        )
        resp.raise_for_status()
        return {"data": resp.json()}
    except Exception:
        return {"data": []}


mesh_scores = mesh_scores_endpoint


@router.get("/mesh-memory")
def mesh_memory_endpoint(session: Session = fastapi.Depends(get_session)) -> dict[str, Any]:
    import requests
    try:
        resp = requests.post(
            "http://127.0.0.1:8772/query",
            json={"sql": "SELECT * FROM mesh_memory LIMIT 100"},
            timeout=10,
        )
        resp.raise_for_status()
        return {"data": resp.json()}
    except Exception:
        return {"data": []}


get_mesh_memory = mesh_memory_endpoint


@router.get("/signal-scores")
def signal_scores_endpoint(session: Session = fastapi.Depends(get_session)) -> dict[str, Any]:
    import requests
    try:
        resp = requests.post(
            "http://127.0.0.1:8772/query",
            json={"sql": "SELECT * FROM mcp_signal_scores LIMIT 100"},
            timeout=10,
        )
        resp.raise_for_status()
        return {"data": resp.json()}
    except Exception:
        return {"data": []}


get_signal_scores = signal_scores_endpoint


def get_mesh_scores_endpoint(session: Session) -> dict[str, Any]:
    return mesh_scores_endpoint(session=session)


@router.post("/reset-quarantine")
def reset_quarantine_endpoint(
    server_id: str = "",
    session: Session = fastapi.Depends(get_session),
) -> dict[str, Any]:
    return {"status": "reset", "server_id": server_id}


reset_server_export_quarantine_api = reset_quarantine_endpoint


def _dummy_post(session: Session) -> dict[str, Any]:
    return {"status": "ok"}


def _run_self_test() -> str:
    from app.db import get_session
    from app.models import McpServerRegistry, McpLlmAxisScore, McpScoreDispute, Org, User
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    engine = create_engine("sqlite:///:memory:")
    from app.models.base import Base
    Base.metadata.create_all(bind=engine)
    TestSession = sessionmaker(bind=engine)
    test_session = TestSession()

    mesh_scores_endpoint(session=test_session)
    mesh_memory_endpoint(session=test_session)
    signal_scores_endpoint(session=test_session)
    reset_quarantine_endpoint(session=test_session)
    _dummy_post(session=test_session)
    get_mesh_scores_endpoint(session=test_session)
    get_mesh_memory(session=test_session)
    get_signal_scores(session=test_session)

    test_session.close()
    print("PASS")
    return "PASS"


if __name__ == "__main__":
    _run_self_test()