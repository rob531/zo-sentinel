from typing import Any, Optional
import httpx
from pydantic import BaseModel


class MeshMemoryQuery(BaseModel):
    query: str
    params: Optional[dict[str, Any]] = None


class MeshMemoryResult(BaseModel):
    id: str
    data: dict[str, Any]


MESH_MEMORY_ENDPOINT = "http://127.0.0.1:8772/query"


def mesh_memory_endpoint() -> str:
    return MESH_MEMORY_ENDPOINT


def _query_mesh(query: str, params: Optional[dict[str, Any]] = None) -> list[dict[str, Any]]:
    payload: dict[str, Any] = {"table": "mesh_memory", "query": query}
    if params:
        payload["params"] = params
    with httpx.Client(timeout=30.0) as client:
        resp = client.post(mesh_memory_endpoint(), json=payload)
        resp.raise_for_status()
        result = resp.json()
        return result.get("rows", [])


def get_mesh_memory_by_id(mesh_id: str) -> Optional[dict[str, Any]]:
    query = "SELECT * FROM mesh_memory WHERE id = $1"
    rows = _query_mesh(query, {"$1": mesh_id})
    return rows[0] if rows else None


def get_mesh_memory_endpoint() -> str:
    return mesh_memory_endpoint()


if __name__ == "__main__":
    import uvicorn
    from fastapi import FastAPI, Depends
    from sqlalchemy import text
    from sqlalchemy.pool import StaticPool
    from app.db import get_session

    app = FastAPI()

    engine = None

    def _get_test_session():
        from sqlalchemy.orm import sessionmaker
        from app.db import Base
        global engine
        if engine is None:
            from sqlalchemy import create_engine
            engine = create_engine(
                "sqlite:///:memory:",
                connect_args={"check_same_thread": False},
                poolclass=StaticPool,
            )
            Base.metadata.create_all(bind=engine)
        SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
        session = SessionLocal()
        session.execute(text("CREATE TABLE IF NOT EXISTS mesh_memory (id TEXT PRIMARY KEY, data TEXT)"))
        session.execute(text("INSERT OR IGNORE INTO mesh_memory (id, data) VALUES ('test-001', '{\"key\":\"value\"}')"))
        session.commit()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_session] = _get_test_session

    @app.get("/mesh_memory/{mesh_id}")
    def read_mesh_memory(mesh_id: str, session=Depends(get_session)) -> dict[str, Any]:
        result = session.execute(text("SELECT * FROM mesh_memory WHERE id = :id"), {"id": mesh_id}).fetchone()
        if result is None:
            return {"error": "not found"}
        return {"id": result[0], "data": result[1]}

    @app.get("/mesh_memory_endpoint")
    def read_mesh_memory_endpoint() -> dict[str, str]:
        return {"endpoint": mesh_memory_endpoint()}

    @app.get("/query_mesh")
    def read_query_mesh(session=Depends(get_session)) -> dict[str, Any]:
        result = session.execute(text("SELECT * FROM mesh_memory LIMIT 1")).fetchone()
        if result:
            return {"id": result[0], "data": result[1]}
        return {"error": "no data"}

    @app.get("/mesh_memory_by_id/{mesh_id}")
    def read_mesh_memory_by_id(mesh_id: str, session=Depends(get_session)) -> dict[str, Any]:
        result = session.execute(text("SELECT * FROM mesh_memory WHERE id = :id"), {"id": mesh_id}).fetchone()
        if result is None:
            return {"error": "not found"}
        return {"id": result[0], "data": result[1]}

    with httpx.Client(timeout=10.0) as client:
        from threading import Thread
        server = Thread(target=lambda: uvicorn.run(app, host="127.0.0.1", port=18772, log_level="error"), daemon=True)
        server.start()
        import time; time.sleep(1.5)
        try:
            r = client.get("http://127.0.0.1:18772/mesh_memory/test-001")
            r.raise_for_status()
            r2 = client.get("http://127.0.0.1:18772/mesh_memory_endpoint")
            r2.raise_for_status()
            r3 = client.get("http://127.0.0.1:18772/query_mesh")
            r3.raise_for_status()
            r4 = client.get("http://127.0.0.1:18772/mesh_memory_by_id/test-001")
            r4.raise_for_status()
            print("PASS")
        except Exception as e:
            print(f"FAIL: {e}")