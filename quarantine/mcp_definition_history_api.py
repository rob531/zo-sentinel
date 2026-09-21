"""
MCP Definition History API

Exposes mcp_definition_history table via FastAPI for analyst review.
"""

import json
import hashlib
from datetime import datetime
from typing import List, Optional

import requests
from fastapi import FastAPI, HTTPException, Header
from pydantic import BaseModel

app = FastAPI(title="MCP Definition History API")

WRITE_SERVICE_URL = "http://127.0.0.1:8772"


def validate_analyst_guid(guid: str) -> bool:
    """Validate analyst GUID via write_service lookup."""
    try:
        response = requests.post(
            f"{WRITE_SERVICE_URL}/query",
            json={
                "sql": "SELECT guid FROM analyst_registry WHERE guid = ?",
                "params": [guid]
            },
            timeout=10
        )
        if response.status_code == 200:
            rows = response.json().get("rows", [])
            return len(rows) > 0
        return False
    except Exception:
        return False


def query_db(sql: str, params: list) -> List[dict]:
    """Execute SELECT query via write_service."""
    response = requests.post(
        f"{WRITE_SERVICE_URL}/query",
        json={"sql": sql, "params": params},
        timeout=30
    )
    response.raise_for_status()
    return response.json().get("rows", [])


def execute_db(sql: str, params: list) -> None:
    """Execute INSERT/UPDATE query via write_service."""
    response = requests.post(
        f"{WRITE_SERVICE_URL}/execute",
        json={"sql": sql, "params": params},
        timeout=30
    )
    response.raise_for_status()


# Response Models
class DefinitionHistoryRecord(BaseModel):
    mcp_name: str
    version: int
    captured_at: str
    definition_hash: str
    tool_count: int
    permission_summary: str


class DiffResponse(BaseModel):
    additions: List[str]
    removals: List[str]
    changes: List[str]


class CaptureResponse(BaseModel):
    captured: bool
    version: int


class TimelineEvent(BaseModel):
    version: int
    captured_at: str
    delta_lines: int


class TimelineResponse(BaseModel):
    events: List[TimelineEvent]


@app.get("/api/definition-history/{mcp_name}", response_model=List[DefinitionHistoryRecord])
def get_definition_history(mcp_name: str, x_analyst_guid: str = Header(...)):
    """Retrieve all history records for an MCP definition."""
    if not validate_analyst_guid(x_analyst_guid):
        raise HTTPException(status_code=401, detail="Invalid analyst GUID")

    rows = query_db(
        "SELECT mcp_name, version, captured_at, definition_hash, tool_count, permission_summary "
        "FROM mcp_definition_history WHERE mcp_name = ? ORDER BY version DESC",
        [mcp_name]
    )

    return [
        DefinitionHistoryRecord(
            mcp_name=r["mcp_name"],
            version=r["version"],
            captured_at=r["captured_at"],
            definition_hash=r["definition_hash"],
            tool_count=r["tool_count"],
            permission_summary=r["permission_summary"]
        )
        for r in rows
    ]


@app.get("/api/definition-history/{mcp_name}/diff/{version_a}/{version_b}", response_model=DiffResponse)
def get_definition_diff(
    mcp_name: str,
    version_a: int,
    version_b: int,
    x_analyst_guid: str = Header(...)
):
    """Get structural diff between two versions of an MCP definition."""
    if not validate_analyst_guid(x_analyst_guid):
        raise HTTPException(status_code=401, detail="Invalid analyst GUID")

    row_a = query_db(
        "SELECT definition_blob FROM mcp_definition_history "
        "WHERE mcp_name = ? AND version = ?",
        [mcp_name, version_a]
    )

    row_b = query_db(
        "SELECT definition_blob FROM mcp_definition_history "
        "WHERE mcp_name = ? AND version = ?",
        [mcp_name, version_b]
    )

    if not row_a or not row_b:
        raise HTTPException(
            status_code=404,
            detail=f"Version(s) not found: {version_a} or {version_b}"
        )

    try:
        def_a = json.loads(row_a[0]["definition_blob"])
        def_b = json.loads(row_b[0]["definition_blob"])
    except (json.JSONDecodeError, KeyError):
        raise HTTPException(status_code=500, detail="Invalid definition blob format")

    additions = []
    removals = []
    changes = []

    keys_a = set(def_a.keys()) if isinstance(def_a, dict) else set()
    keys_b = set(def_b.keys()) if isinstance(def_b, dict) else set()

    for key in keys_b - keys_a:
        additions.append(key)

    for key in keys_a - keys_b:
        removals.append(key)

    for key in keys_a & keys_b:
        if def_a[key] != def_b[key]:
            changes.append(f"{key}: {def_a[key]} -> {def_b[key]}")

    return DiffResponse(additions=additions, removals=removals, changes=changes)


@app.post("/api/definition-history/capture/{mcp_name}", response_model=CaptureResponse)
def capture_definition(mcp_name: str, x_analyst_guid: str = Header(...)):
    """Capture current definition as new version (if changed)."""
    if not validate_analyst_guid(x_analyst_guid):
        raise HTTPException(status_code=401, detail="Invalid analyst GUID")

    registry_rows = query_db(
        "SELECT definition_blob FROM mcp_server_registry WHERE mcp_name = ?",
        [mcp_name]
    )

    if not registry_rows:
        raise HTTPException(status_code=404, detail=f"MCP '{mcp_name}' not found in registry")

    definition_blob = registry_rows[0]["definition_blob"]
    current_hash = hashlib.sha256(definition_blob.encode()).hexdigest()

    try:
        parsed_def = json.loads(definition_blob)
    except json.JSONDecodeError:
        parsed_def = {}

    tool_count = len(parsed_def.get("tools", [])) if isinstance(parsed_def, dict) else 0
    perms = parsed_def.get("permissions", []) if isinstance(parsed_def, dict) else []
    permission_summary = ",".join(sorted(perms)) if isinstance(perms, list) else str(perms)

    history_rows = query_db(
        "SELECT definition_hash, version FROM mcp_definition_history "
        "WHERE mcp_name = ? ORDER BY version DESC LIMIT 1",
        [mcp_name]
    )

    if history_rows and history_rows[0]["definition_hash"] == current_hash:
        return CaptureResponse(captured=False, version=history_rows[0]["version"])

    new_version = 1
    if history_rows:
        new_version = history_rows[0]["version"] + 1

    captured_at = datetime.utcnow().isoformat()

    execute_db(
        "INSERT INTO mcp_definition_history "
        "(mcp_name, version, captured_at, definition_hash, definition_blob, tool_count, permission_summary) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        [mcp_name, new_version, captured_at, current_hash, definition_blob, tool_count, permission_summary]
    )

    return CaptureResponse(captured=True, version=new_version)


@app.get("/api/definition-history/{mcp_name}/timeline", response_model=TimelineResponse)
def get_definition_timeline(mcp_name: str, x_analyst_guid: str = Header(...)):
    """Get timeline of version captures with delta lines."""
    if not validate_analyst_guid(x_analyst_guid):
        raise HTTPException(status_code=401, detail="Invalid analyst GUID")

    rows = query_db(
        "SELECT version, captured_at, tool_count FROM mcp_definition_history "
        "WHERE mcp_name = ? ORDER BY version ASC",
        [mcp_name]
    )

    events = []
    prev_tool_count = None

    for r in rows:
        tool_count = r["tool_count"]
        if prev_tool_count is None:
            delta_lines = 0
        else:
            delta_lines = tool_count - prev_tool_count

        events.append(TimelineEvent(
            version=r["version"],
            captured_at=r["captured_at"],
            delta_lines=delta_lines
        ))
        prev_tool_count = tool_count

    return TimelineResponse(events=events)


if __name__ == '__main__':
    import mcp_definition_history_api
    assert hasattr(mcp_definition_history_api, 'app')
    routes = [r.path for r in mcp_definition_history_api.app.routes]
    assert '/api/definition-history/{mcp_name}' in routes
    assert '/api/definition-history/{mcp_name}/diff/{version_a}/{version_b}' in routes
    assert '/api/definition-history/capture/{mcp_name}' in routes
    print("PASS: mcp_definition_history_api routes registered")