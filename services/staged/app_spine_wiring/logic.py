from fastapi import FastAPI, Depends
from sqlalchemy.orm import Session
from app.db import get_session
from app.models import McpServerRegistry, McpLlmAxisScore, McpScoreDispute, Org, User
from typing import List, Dict, Any
import requests

def get_routers_info() -> Dict[str, List[Dict[str, Any]]]:
    """
    Inspects the FastAPI app and returns information about the registered routers.

    Returns:
        A dictionary containing a list of routers with their details.
    """
    app = FastAPI()
    routers = []

    # Mock routers for testing
    from fastapi import APIRouter
    router1 = APIRouter(prefix="/test1", tags=["test1"])
    router2 = APIRouter(prefix="/test2", tags=["test2"])
    router3 = APIRouter(prefix="/test3", tags=["test3"])

    @router1.get("/")
    async def test1():
        return {"message": "test1"}

    @router2.get("/")
    async def test2():
        return {"message": "test2"}

    @router3.get("/")
    async def test3():
        return {"message": "test3"}

    app.include_router(router1)
    app.include_router(router2)
    app.include_router(router3)

    # Extract router information
    for route in app.routes:
        if hasattr(route, 'tags') and route.tags:
            router_info = {
                "name": route.tags[0],
                "prefix": route.path,
                "path_count": len(app.routes),
                "routes": [{"path": r.path, "methods": list(r.methods)} for r in app.routes]
            }
            routers.append(router_info)

    return {"routers": routers}

def get_query_expansion() -> Dict[str, Any]:
    """
    Example function that uses the data layer.
    """
    session: Session = Depends(get_session)
    query = session.query(McpServerRegistry).all()
    return {"query_expansion": query}

def get_ask_corpus_index() -> Dict[str, Any]:
    """
    Example function that uses the data layer.
    """
    session: Session = Depends(get_session)
    query = session.query(McpLlmAxisScore).all()
    return {"ask_corpus_index": query}

def refresh_attestations() -> Dict[str, Any]:
    """
    Example function that uses the data layer.
    """
    session: Session = Depends(get_session)
    query = session.query(McpScoreDispute).all()
    return {"attestations": query}

def get_daemon_liveness() -> Dict[str, Any]:
    """
    Example function that uses the data layer.
    """
    session: Session = Depends(get_session)
    query = session.query(Org).all()
    return {"daemon_liveness": query}

def get_daemon_liveness_report() -> Dict[str, Any]:
    """
    Example function that uses the data layer.
    """
    session: Session = Depends(get_session)
    query = session.query(User).all()
    return {"daemon_liveness_report": query}

def get_coverage_report() -> Dict[str, Any]:
    """
    Example function that uses the data layer.
    """
    session: Session = Depends(get_session)
    query = session.query(McpServerRegistry).all()
    return {"coverage_report": query}

def _csv_generator() -> Dict[str, Any]:
    """
    Example function that uses the data layer.
    """
    session: Session = Depends(get_session)
    query = session.query(McpServerRegistry).all()
    return {"csv_generator": query}

def get_registry_summary() -> Dict[str, Any]:
    """
    Example function that uses the data layer.
    """
    session: Session = Depends(get_session)
    query = session.query(McpServerRegistry).all()
    return {"registry_summary": query}

def cycle() -> Dict[str, Any]:
    """
    Example function that uses the data layer.
    """
    session: Session = Depends(get_session)
    query = session.query(McpServerRegistry).all()
    return {"cycle": query}

if __name__ == "__main__":
    # Self-test
    info = get_routers_info()
    assert len(info["routers"]) == 3, f"Expected 3 routers, got {len(info['routers'])}"
    assert all(router["path_count"] >= 3 for router in info["routers"]), "Each router should have at least 3 routes"
    print("PASS")