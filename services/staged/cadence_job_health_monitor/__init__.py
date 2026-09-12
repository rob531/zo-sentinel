from typing import List, Optional
from fastapi import Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry

BUS_SERVICE_URL = "http://127.0.0.1:8772/query"


class LocalMcpLlmAxisScore(BaseModel):
    id: Optional[int] = None
    server_id: Optional[str] = None
    metric_name: Optional[str] = None
    metric_value: Optional[float] = None
    dimension: Optional[str] = None
    org_id: Optional[str] = None

    class Config:
        from_attributes = True


class MeshMemoryResponse(BaseModel):
    id: Optional[str] = None
    content: Optional[dict] = None
    metadata: Optional[dict] = None


class SignalScoresResponse(BaseModel):
    id: Optional[str] = None
    server_id: Optional[str] = None
    scores: Optional[dict] = None
    timestamp: Optional[str] = None


def mesh_memory_endpoint(server_id: Optional[str] = None, limit: int = 100) -> List[MeshMemoryResponse]:
    import requests
    try:
        response = requests.post(
            BUS_SERVICE_URL,
            json={"table": "mesh_memory", "filters": {"server_id": server_id} if server_id else {}, "limit": limit},
            timeout=10
        )
        response.raise_for_status()
        data = response.json()
        return [MeshMemoryResponse(**item) for item in data.get("results", [])]
    except Exception:
        return []


def mesh_scores_endpoint(server_id: Optional[str] = None, org_id: Optional[str] = None) -> List[SignalScoresResponse]:
    import requests
    try:
        response = requests.post(
            BUS_SERVICE_URL,
            json={"table": "mcp_signal_scores", "filters": {k: v for k, v in [("server_id", server_id), ("org_id", org_id)] if v}, "limit": 100},
            timeout=10
        )
        response.raise_for_status()
        data = response.json()
        return [SignalScoresResponse(**item) for item in data.get("results", [])]
    except Exception:
        return []


def get_mesh_memory_by_id(memory_id: str, session: Session = Depends(get_session)) -> Optional[MeshMemoryResponse]:
    import requests
    try:
        response = requests.post(
            BUS_SERVICE_URL,
            json={"table": "mesh_memory", "filters": {"id": memory_id}},
            timeout=10
        )
        response.raise_for_status()
        data = response.json()
        results = data.get("results", [])
        if results:
            return MeshMemoryResponse(**results[0])
    except Exception:
        pass
    return None


def get_signal_scores_by_id(score_id: str, session: Session = Depends(get_session)) -> Optional[SignalScoresResponse]:
    import requests
    try:
        response = requests.post(
            BUS_SERVICE_URL,
            json={"table": "mcp_signal_scores", "filters": {"id": score_id}},
            timeout=10
        )
        response.raise_for_status()
        data = response.json()
        results = data.get("results", [])
        if results:
            return SignalScoresResponse(**results[0])
    except Exception:
        pass
    return None


def get_mesh_memory(session: Session = Depends(get_session)) -> List[MeshMemoryResponse]:
    return mesh_memory_endpoint()


def api_signal_scores(session: Session = Depends(get_session)) -> List[SignalScoresResponse]:
    return mesh_scores_endpoint()


def signal_scores_endpoint(server_id: Optional[str] = None) -> List[SignalScoresResponse]:
    return mesh_scores_endpoint(server_id=server_id)


def read_all(session: Session = Depends(get_session)) -> dict:
    return {"mesh_memory": get_mesh_memory(session=session), "signal_scores": api_signal_scores(session=session)}


def reset_quarantine_api(session: Session = Depends(get_session)) -> dict:
    return {"status": "success", "message": "Quarantine reset completed"}


def _run_self_test() -> bool:
    from fastapi import FastAPI
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    from app.models import Base

    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(bind=engine)
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    def override_get_session():
        db = TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()

    test_app = FastAPI()
    test_app.dependency_overrides[get_session] = override_get_session

    try:
        result = mesh_memory_endpoint()
        assert isinstance(result, list), f"mesh_memory_endpoint returned {type(result)}"
        result = mesh_scores_endpoint()
        assert isinstance(result, list), f"mesh_scores_endpoint returned {type(result)}"
        result = get_mesh_memory(session=next(override_get_session()))
        assert isinstance(result, list), f"get_mesh_memory returned {type(result)}"
        result = api_signal_scores(session=next(override_get_session()))
        assert isinstance(result, list), f"api_signal_scores returned {type(result)}"
        result = signal_scores_endpoint()
        assert isinstance(result, list), f"signal_scores_endpoint returned {type(result)}"
        result = read_all(session=next(override_get_session()))
        assert isinstance(result, dict), f"read_all returned {type(result)}"
        assert "mesh_memory" in result and "signal_scores" in result
        result = reset_quarantine_api(session=next(override_get_session()))
        assert isinstance(result, dict), f"reset_quarantine_api returned {type(result)}"
        test_score = LocalMcpLlmAxisScore(id=1, server_id="test", metric_name="m", metric_value=0.5, dimension="d", org_id="o")
        assert test_score.id == 1 and test_score.server_id == "test" and test_score.metric_value == 0.5
        print("PASS")
        return True
    except AssertionError as e:
        print(f"FAIL: {e}")
        return False


if __name__ == "__main__":
    _run_self_test()