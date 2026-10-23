# Auto-emitted service package
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry

router = APIRouter()

MESH_BUS_URL = "http://127.0.0.1:8772/query"


class MeshMemoryEntry(BaseModel):
    id: Optional[int] = None
    key: str
    value: Any
    metadata: Optional[Dict[str, Any]] = None


class MeshMemoryEndpoint:
    def __init__(self, session: Session):
        self.session = session
        self.bus_url = MESH_BUS_URL

    def get(self, key: str) -> Optional[MeshMemoryEntry]:
        with httpx.Client(timeout=10.0) as client:
            resp = client.post(
                self.bus_url,
                json={
                    "sql": "SELECT id, key, value, metadata FROM mesh_memory WHERE key = $1",
                    "params": [key],
                },
            )
            resp.raise_for_status()
            rows = resp.json()
            if not rows:
                return None
            row = rows[0]
            return MeshMemoryEntry(
                id=row["id"],
                key=row["key"],
                value=row["value"],
                metadata=row.get("metadata"),
            )

    def put(self, key: str, value: Any, metadata: Optional[Dict[str, Any]] = None) -> MeshMemoryEntry:
        with httpx.Client(timeout=10.0) as client:
            meta_json = json.dumps(metadata) if metadata else None
            resp = client.post(
                self.bus_url,
                json={
                    "sql": "INSERT INTO mesh_memory (key, value, metadata) VALUES ($1, $2, $3) RETURNING id, key, value, metadata",
                    "params": [key, json.dumps(value), meta_json],
                },
            )
            resp.raise_for_status()
            rows = resp.json()
            row = rows[0]
            return MeshMemoryEntry(
                id=row["id"],
                key=row["key"],
                value=row["value"],
                metadata=row.get("metadata"),
            )

    def list_(self, prefix: Optional[str] = None) -> List[MeshMemoryEntry]:
        if prefix:
            sql = "SELECT id, key, value, metadata FROM mesh_memory WHERE key LIKE $1"
            params = [f"{prefix}%"]
        else:
            sql = "SELECT id, key, value, metadata FROM mesh_memory"
            params = []
        with httpx.Client(timeout=10.0) as client:
            resp = client.post(self.bus_url, json={"sql": sql, "params": params})
            resp.raise_for_status()
            rows = resp.json()
            return [
                MeshMemoryEntry(
                    id=r["id"],
                    key=r["key"],
                    value=r["value"],
                    metadata=r.get("metadata"),
                )
                for r in rows
            ]


def mesh_memory_endpoint(session: Session = Depends(get_session)) -> MeshMemoryEndpoint:
    return MeshMemoryEndpoint(session)


def get_mesh_memory_endpoint(session: Session = Depends(get_session)) -> MeshMemoryEndpoint:
    return MeshMemoryEndpoint(session)


@router.get("/mesh-memory/{key}")
def get_mesh_memory(key: str, endpoint: MeshMemoryEndpoint = Depends(mesh_memory_endpoint)) -> MeshMemoryEntry:
    entry = endpoint.get(key)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"Mesh memory key not found: {key}")
    return entry


@router.put("/mesh-memory/{key}")
def put_mesh_memory(
    key: str,
    value: Any,
    metadata: Optional[Dict[str, Any]] = None,
    endpoint: MeshMemoryEndpoint = Depends(mesh_memory_endpoint),
) -> MeshMemoryEntry:
    return endpoint.put(key, value, metadata)


@router.get("/mesh-memory")
def list_mesh_memory(
    prefix: Optional[str] = None,
    endpoint: MeshMemoryEndpoint = Depends(mesh_memory_endpoint),
) -> List[MeshMemoryEntry]:
    return endpoint.list_(prefix)


def run_self_test() -> bool:
    from fastapi import FastAPI
    from unittest.mock import MagicMock
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    with open("app/models.py") as f:
        code = f.read()

    namespace = {}
    exec(compile(code, "app/models.py", "exec"), namespace)
    Base = namespace.get("Base")
    if Base is not None:
        Base.metadata.create_all(bind=engine)

    TestingSession = sessionmaker(bind=engine)
    test_session = TestingSession()

    mock_server = namespace.get("McpServerRegistry")(
        id=1,
        name="test_server",
        endpoint="http://localhost:8000",
        enabled=True,
    )
    test_session.add(mock_server)
    test_session.commit()

    app = FastAPI()
    app.dependency_overrides[get_session] = lambda: test_session
    app.include_router(router)

    from fastapi.testclient import TestClient

    client = TestClient(app)

    put_resp = client.put("/mesh-memory/test_key", json={"value": "test_data", "metadata": {"tag": "test"}})
    if put_resp.status_code != 200:
        print(f"PUT failed: {put_resp.status_code} {put_resp.text}")
        return False

    get_resp = client.get("/mesh-memory/test_key")
    if get_resp.status_code != 200:
        print(f"GET failed: {get_resp.status_code} {get_resp.text}")
        return False

    list_resp = client.get("/mesh-memory")
    if list_resp.status_code != 200:
        print(f"LIST failed: {list_resp.status_code} {list_resp.text}")
        return False

    endpoint = MeshMemoryEndpoint(test_session)
    entries = endpoint.list_()
    if not entries:
        print("Direct list_() returned empty")
        return False

    entry = endpoint.get("test_key")
    if entry is None:
        print("Direct get() returned None for existing key")
        return False

    test_session.close()
    engine.dispose()
    return True


if __name__ == "__main__":
    if run_self_test():
        print("PASS")
    else:
        print("FAIL")