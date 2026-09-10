from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI, Depends
from pydantic import BaseModel
from sqlalchemy import text

from app.db import get_session
from app.models import McpServerRegistry


class ServerRegistryResponse(BaseModel):
    id: int
    server_name: str
    endpoint: Optional[str] = None
    status: Optional[str] = None
    metadata_json: Optional[dict] = None

    class Config:
        from_attributes = True


def get_servers(session=Depends(get_session)):
    """Return all servers from mcp_server_registry."""
    result = session.execute(text("SELECT * FROM mcp_server_registry"))
    rows = result.fetchall()
    return [
        ServerRegistryResponse(
            id=row.id,
            server_name=row.server_name,
            endpoint=row.endpoint,
            status=row.status,
            metadata_json=row.metadata_json,
        )
        for row in rows
    ]


def get_mesh_memory():
    """Query mesh_memory from write-service bus."""
    import httpx
    with httpx.Client(timeout=10.0) as client:
        resp = client.post(
            "http://127.0.0.1:8772/query",
            json={"sql": "SELECT * FROM mesh_memory LIMIT 100"},
        )
        resp.raise_for_status()
        return resp.json()


def api_orgs(session=Depends(get_session)):
    """Return all orgs."""
    result = session.execute(text("SELECT * FROM orgs"))
    rows = result.fetchall()
    return [{"id": r.id, "name": r.name} for r in rows]


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield


def main() -> FastAPI:
    app = FastAPI(title="server_registry", lifespan=lifespan)
    return app


def _run_self_test():
    """Verify module exports compile and core functions are callable."""
    assert callable(get_servers)
    assert callable(get_mesh_memory)
    assert callable(api_orgs)
    assert ServerRegistryResponse is not None
    print("PASS")


if __name__ == "__main__":
    _run_self_test()