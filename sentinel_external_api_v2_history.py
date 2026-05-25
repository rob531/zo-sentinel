from fastapi import APIRouter, Query, HTTPException, Depends
from pydantic import BaseModel, Field
from typing import Optional, List
import re

WRITE_SERVICE_URL = "http://127.0.0.1:8772/write"
QUERY_URL = "http://127.0.0.1:8772/query"

SERVER_ID_PATTERN = re.compile(r'^[a-f0-9]{32}$')


class DefinitionHistoryRecord(BaseModel):
    snapshot_type: Optional[str] = Field(None, description="Type of snapshot: initial, update, or override")
    captured_at: Optional[str] = Field(None, description="ISO timestamp when this change was captured")
    prev_verdict: Optional[str] = Field(None, description="Previous verdict before the change")
    new_verdict: Optional[str] = Field(None, description="New verdict after the change")
    change_reason: Optional[str] = Field(None, description="Reason for the verdict change")
    tool_count: Optional[int] = Field(None, description="Number of tools at time of capture")


def ws_query(sql: str) -> dict:
    import requests
    resp = requests.post(QUERY_URL, json={"sql": sql}, timeout=30)
    resp.raise_for_status()
    return resp.json()


def server_exists(server_id: str, ws_query_fn) -> bool:
    result = ws_query_fn(f"SELECT 1 FROM mcp_server_registry WHERE server_id = '{server_id}' LIMIT 1")
    return result.get('rows') and len(result['rows']) > 0


def get_rate_limiter():
    from sentinel_external_api import get_rate_limiter
    return get_rate_limiter()


def enforce_rate_limit(client_id: str = Depends(lambda: "default")):
    limiter = get_rate_limiter()
    if limiter:
        from sentinel_external_api import enforce_rate_limit as enf_rate
        return enf_rate(client_id)
    return None


def register_routes(app):
    history_router = APIRouter(prefix="/v1/mcp", tags=["History"])

    @history_router.get(
        "/{server_id}/history",
        response_model=List[DefinitionHistoryRecord],
        summary="Get MCP server definition history",
        description="Returns the verdict and definition change timeline for an MCP server",
        dependencies=[Depends(enforce_rate_limit)]
    )
    async def get_mcp_history(
        server_id: str,
        limit: int = Query(20, ge=1, le=100, description="Maximum number of history records to return")
    ):
        if not SERVER_ID_PATTERN.match(server_id):
            raise HTTPException(
                status_code=400,
                detail=f"Invalid server_id format. Expected 32-character hex string, got: {server_id}"
            )

        if not server_exists(server_id, ws_query):
            raise HTTPException(
                status_code=404,
                detail=f"MCP server not found: {server_id}"
            )

        history_sql = f"""
            SELECT 
                snapshot_type,
                captured_at,
                prev_verdict,
                new_verdict,
                change_reason,
                tool_count
            FROM mcp_definition_history
            WHERE server_id = '{server_id}'
            ORDER BY captured_at DESC
            LIMIT {limit}
        """

        result = ws_query(history_sql)
        rows = result.get('rows', [])

        if not rows:
            return []

        records = []
        for row in rows:
            record = DefinitionHistoryRecord(
                snapshot_type=row.get('snapshot_type') if isinstance(row, dict) else None,
                captured_at=str(row.get('captured_at')) if isinstance(row, dict) and row.get('captured_at') else None,
                prev_verdict=row.get('prev_verdict') if isinstance(row, dict) else None,
                new_verdict=row.get('new_verdict') if isinstance(row, dict) else None,
                change_reason=row.get('change_reason') if isinstance(row, dict) else None,
                tool_count=int(row.get('tool_count')) if isinstance(row, dict) and row.get('tool_count') is not None else None
            )
            records.append(record)

        return records

    app.include_router(history_router)


def run():
    from fastapi import FastAPI
    import uvicorn

    app = FastAPI(title="ZO-Sentinel History API Extension")

    @app.get("/health")
    async def health():
        return {"status": "ok", "service": "sentinel_external_api_v2_history"}

    register_routes(app)
    uvicorn.run(app, host="127.0.0.1", port=8786)


if __name__ == "__main__":
    run()