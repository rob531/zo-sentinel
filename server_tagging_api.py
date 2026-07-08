from fastapi import APIRouter, Depends, HTTPException, Header, Request
from pydantic import BaseModel
from typing import Optional, List, Any, Dict, Callable
import requests
from datetime import datetime

router = APIRouter(prefix="/servers", tags=["server-tagging"])

TAG_SERVICE_URL = "http://127.0.0.1:8772"
TABLE_NAME = "server_tags"

class TagCreate(BaseModel):
    tag: str

class TagResponse(BaseModel):
    id: int
    server_id: str
    tag: str
    org_id: str
    created_by: Optional[str] = None
    created_at: Optional[datetime] = None

def get_write_service() -> Callable:
    def _execute(sql: str, params: Optional[Dict] = None) -> List[Dict]:
        resp = requests.post(
            f"{TAG_SERVICE_URL}/execute",
            json={"sql": sql, "params": params or {}}
        )
        if resp.status_code != 200:
            raise HTTPException(status_code=500, detail="Write service execution failed")
        return resp.json().get("rows", [])
    
    def _query(sql: str, params: Optional[Dict] = None) -> List[Dict]:
        resp = requests.post(
            f"{TAG_SERVICE_URL}/query",
            json={"sql": sql, "params": params or {}}
        )
        if resp.status_code != 200:
            raise HTTPException(status_code=500, detail="Write service execution failed")
        return resp.json().get("rows", [])
    
    return _execute, _query

_write_service_factory = {"factory": get_write_service}

def set_write_service_factory(factory):
    _write_service_factory["factory"] = factory

def _get_write_service():
    return _write_service_factory["factory"]()

def create_server_tags_table():
    execute, _ = _get_write_service()
    sql = f"""CREATE TABLE IF NOT EXISTS {TABLE_NAME} (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        server_id TEXT NOT NULL,
        tag TEXT NOT NULL,
        org_id TEXT NOT NULL,
        created_by TEXT,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(server_id, tag)
    )"""
    execute(sql)

@router.get("/{server_id}/tags", response_model=List[TagResponse])
def list_tags(
    server_id: str,
    x_org_id: str = Header(...)
):
    _, query = _get_write_service()
    sql = f"SELECT id, server_id, tag, org_id, created_by, created_at FROM {TABLE_NAME} WHERE org_id = :org_id AND server_id = :server_id"
    rows = query(sql, {"org_id": x_org_id, "server_id": server_id})
    return [TagResponse(**row) for row in rows]

@router.post("/{server_id}/tags", response_model=TagResponse)
def add_tag(
    request: Request,
    server_id: str,
    x_org_id: str = Header(...),
    x_user_id: Optional[str] = Header(None)
):
    body = request.state.body if hasattr(request.state, "body") else None
    if body is None:
        body = TagCreate(tag="")
    
    execute, query = _get_write_service()
    
    existing = query(
        f"SELECT id, server_id, tag, org_id, created_by, created_at FROM {TABLE_NAME} WHERE org_id = :org_id AND server_id = :server_id AND tag = :tag",
        {"org_id": x_org_id, "server_id": server_id, "tag": body.tag}
    )
    if existing:
        return TagResponse(**existing[0])
    
    sql = f"INSERT INTO {TABLE_NAME} (server_id, tag, org_id, created_by) VALUES (:server_id, :tag, :org_id, :created_by)"
    result = execute(sql, {"server_id": server_id, "tag": body.tag, "org_id": x_org_id, "created_by": x_user_id})
    
    row = query(
        f"SELECT id, server_id, tag, org_id, created_by, created_at FROM {TABLE_NAME} WHERE org_id = :org_id AND server_id = :server_id AND tag = :tag",
        {"org_id": x_org_id, "server_id": server_id, "tag": body.tag}
    )
    return TagResponse(**row[0])

@router.delete("/{server_id}/tags/{tag}")
def remove_tag(
    server_id: str,
    tag: str,
    x_org_id: str = Header(...)
):
    execute, query = _get_write_service()
    existing = query(
        f"SELECT id FROM {TABLE_NAME} WHERE org_id = :org_id AND server_id = :server_id AND tag = :tag",
        {"org_id": x_org_id, "server_id": server_id, "tag": tag}
    )
    if not existing:
        raise HTTPException(status_code=404, detail="Tag not found")
    
    execute(
        f"DELETE FROM {TABLE_NAME} WHERE org_id = :org_id AND server_id = :server_id AND tag = :tag",
        {"org_id": x_org_id, "server_id": server_id, "tag": tag}
    )
    return {"status": "deleted"}

if __name__ == "__main__":
    from starlette.testclient import TestClient
    from contextlib import asynccontextmanager
    import json

    class MockWriteService:
        def __init__(self):
            self.data: List[Dict[str, Any]] = []
            self.next_id = 1

        def execute(self, sql: str, params: Optional[Dict] = None) -> List[Dict]:
            params = params or {}
            sql_upper = sql.strip().upper()
            
            if sql_upper.startswith("CREATE TABLE"):
                return []
            
            if sql_upper.startswith("INSERT INTO"):
                self.data.append({
                    "id": self.next_id,
                    "server_id": params.get("server_id"),
                    "tag": params.get("tag"),
                    "org_id": params.get("org_id"),
                    "created_by": params.get("created_by"),
                    "created_at": datetime.now().isoformat()
                })
                self.next_id += 1
                return []
            
            if sql_upper.startswith("DELETE FROM"):
                self.data[:] = [r for r in self.data if not (
                    r["org_id"] == params.get("org_id") and
                    r["server_id"] == params.get("server_id") and
                    r["tag"] == params.get("tag")
                )]
                return []

            return []

        def query(self, sql: str, params: Optional[Dict] = None) -> List[Dict]:
            params = params or {}
            sql_upper = sql.strip().upper()
            
            if "SELECT" in sql_upper and "FROM" in sql_upper:
                if "DELETE" not in sql_upper:
                    results = []
                    for r in self.data:
                        if (r["org_id"] == params.get("org_id") and 
                            r["server_id"] == params.get("server_id")):
                            if "tag" not in params or r["tag"] == params.get("tag"):
                                results.append(r)
                    return results
            return []

    mock = MockWriteService()

    def mock_factory():
        return mock.execute, mock.query

    set_write_service_factory(mock_factory)

    from fastapi import FastAPI
    import asyncio
    
    app = FastAPI()
    app.include_router(router)

    @app.middleware("http")
    async def capture_body(request: Request, call_next):
        if request.method in ("POST", "PUT", "PATCH"):
            body = await request.body()
            request.state.body = TagCreate.model_validate_json(body)
        return await call_next(request)

    client = TestClient(app)

    create_server_tags_table()

    client.post("/servers/srv1/tags", json={"tag": "production"}, headers={"X-Org-ID": "org1"})
    client.post("/servers/srv1/tags", json={"tag": "critical"}, headers={"X-Org-ID": "org1"})
    
    resp = client.get("/servers/srv1/tags", headers={"X-Org-ID": "org1"})
    assert len(resp.json()) == 2, f"Expected 2 tags, got {len(resp.json())}"

    client.delete("/servers/srv1/tags/production", headers={"X-Org-ID": "org1"})
    
    resp = client.get("/servers/srv1/tags", headers={"X-Org-ID": "org1"})
    assert len(resp.json()) == 1, f"Expected 1 tag, got {len(resp.json())}"

    print("PASS")