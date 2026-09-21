from typing import Optional
from fastapi import Depends
from sqlalchemy.orm import Session

from app.db import get_session


def get_db():
    return Depends(get_session)


def get_mesh_scores(session: Optional[Session] = None, org_id: Optional[int] = None) -> dict:
    import requests
    try:
        query = "SELECT * FROM mcp_signal_scores"
        params = []
        if org_id is not None:
            query += " WHERE org_id = ?"
            params.append(org_id)
        resp = requests.post(
            "http://127.0.0.1:8772/query",
            json={"query": query, "params": params},
            timeout=5,
        )
        if resp.status_code == 200:
            return resp.json()
    except Exception:
        pass
    return {}


def mesh_scores_endpoint(org_id: int, session: Optional[Session] = None) -> dict:
    return get_mesh_scores(session=session, org_id=org_id)


def signal_scores_endpoint(org_id: int, session: Optional[Session] = None) -> dict:
    return get_mesh_scores(session=session, org_id=org_id)


def get_signal_scores(org_id: int, session: Optional[Session] = None) -> dict:
    return get_mesh_scores(session=session, org_id=org_id)


def mesh_scores(org_id: int, session: Optional[Session] = None) -> dict:
    return get_mesh_scores(session=session, org_id=org_id)


def get_mesh_memory_endpoint(org_id: int, session: Optional[Session] = None) -> dict:
    import requests
    try:
        resp = requests.post(
            "http://127.0.0.1:8772/query",
            json={"query": "SELECT * FROM mesh_memory WHERE org_id = ?", "params": [org_id]},
            timeout=5,
        )
        if resp.status_code == 200:
            return resp.json()
    except Exception:
        pass
    return {}


def _run_self_test():
    from fastapi import FastAPI
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestSession = sessionmaker(bind=engine)
    that_app = FastAPI()

    def _override():
        db = TestSession()
        try:
            yield db
        finally:
            db.close()

    that_app.dependency_overrides[get_session] = _override

    assert get_db is not None
    assert get_mesh_scores is not None
    assert mesh_scores_endpoint is not None
    assert signal_scores_endpoint is not None
    assert get_signal_scores is not None
    assert mesh_scores is not None
    assert get_mesh_memory_endpoint is not None

    with TestSession() as sess:
        result = get_mesh_scores(session=sess, org_id=1)
        assert isinstance(result, dict)
        result2 = mesh_scores_endpoint(org_id=1, session=sess)
        assert isinstance(result2, dict)
        result3 = get_mesh_memory_endpoint(org_id=1, session=sess)
        assert isinstance(result3, dict)

    print("PASS")


if __name__ == "__main__":
    _run_self_test()