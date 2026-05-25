import re
from datetime import datetime, timedelta
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

WRITE_SERVICE_URL = "http://127.0.0.1:8772"
QUERY_SERVICE_URL = "http://127.0.0.1:8772"

SERVER_ID_PATTERN = re.compile(r'^[a-f0-9]{32}$')

class AttestationRecord(BaseModel):
    attestation_type: Optional[str] = Field(None, description="Type of attestation performed")
    attested_by: Optional[str] = Field(None, description="Entity that performed the attestation")
    attested_at: Optional[str] = Field(None, description="ISO timestamp when attestation occurred")
    verdict_at_attestation: Optional[str] = Field(None, description="Trust verdict at time of attestation")
    attestation_notes: Optional[str] = Field(None, description="Additional notes from the attestation")
    is_current: bool = Field(False, description="True if attestation is less than 30 days old")

def ws_query(sql: str, params: tuple = ()):
    import requests
    try:
        resp = requests.post(
            QUERY_SERVICE_URL,
            json={"sql": sql, "params": params},
            timeout=10
        )
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        return {"rows": [], "error": str(e)}

def get_rate_limiter():
    from sentinel_external_api import get_rate_limiter
    return get_rate_limiter()

def enforce_rate_limit(request: Request):
    limiter = get_rate_limiter()
    if limiter:
        client_ip = request.client.host if request.client else "unknown"
        if not limiter.is_allowed(client_ip):
            raise HTTPException(status_code=429, detail="Rate limit exceeded")
    return True

def validate_server_id(server_id: str) -> bool:
    return bool(SERVER_ID_PATTERN.match(server_id))

def register_routes(app):
    router = APIRouter(prefix="/v1/mcp", tags=["attestation"])

    @router.get(
        "/{server_id}/attestation",
        response_model=AttestationRecord,
        dependencies=[Depends(enforce_rate_limit)]
    )
    async def get_server_attestation(server_id: str):
        if not validate_server_id(server_id):
            raise HTTPException(status_code=400, detail="Invalid server_id format. Expected 32-character hex string.")

        registry_check = ws_query(
            "SELECT server_id FROM mcp_server_registry WHERE server_id = ?",
            (server_id,)
        )
        if not registry_check.get("rows"):
            raise HTTPException(status_code=404, detail=f"Server {server_id} not found in registry")

        attestation_result = ws_query(
            "SELECT attestation_type, attested_by, attested_at, verdict_at_attestation, attestation_notes, created_at "
            "FROM mcp_attestations WHERE server_id = ? ORDER BY created_at DESC LIMIT 1",
            (server_id,)
        )

        rows = attestation_result.get("rows", [])
        if not rows:
            return AttestationRecord(
                attestation_type=None,
                attested_by=None,
                attested_at=None,
                verdict_at_attestation=None,
                attestation_notes=None,
                is_current=False
            )

        row = rows[0]
        attestation_type = row.get("attestation_type") if row.get("attestation_type") not in (None, "") else None
        attested_by = row.get("attested_by") if row.get("attested_by") not in (None, "") else None
        attested_at = row.get("attested_at") if row.get("attested_at") not in (None, "") else None
        verdict_at_attestation = row.get("verdict_at_attestation") if row.get("verdict_at_attestation") not in (None, "") else None
        attestation_notes = row.get("attestation_notes") if row.get("attestation_notes") not in (None, "") else None

        created_at_str = row.get("created_at")
        is_current = False
        if created_at_str:
            try:
                if isinstance(created_at_str, str):
                    created_at = datetime.fromisoformat(created_at_str.replace("Z", "+00:00"))
                else:
                    created_at = created_at_str
                cutoff = datetime.now() - timedelta(days=30)
                if created_at > cutoff:
                    is_current = True
            except (ValueError, TypeError):
                is_current = False

        return AttestationRecord(
            attestation_type=attestation_type,
            attested_by=attested_by,
            attested_at=attested_at,
            verdict_at_attestation=verdict_at_attestation,
            attestation_notes=attestation_notes,
            is_current=is_current
        )

    app.include_router(router)

if __name__ == "__main__":
    from fastapi import FastAPI
    import uvicorn

    app = FastAPI(title="Sentinel External API - Attestation Extension")

    try:
        from sentinel_external_api import setup_logging, load_api_keys
        setup_logging()
        load_api_keys()
    except ImportError:
        pass

    register_routes(app)

    @app.get("/health")
    async def health():
        return {"status": "ok", "service": "sentinel_external_api_v2_attestation"}

    uvicorn.run(app, host="127.0.0.1", port=8782)