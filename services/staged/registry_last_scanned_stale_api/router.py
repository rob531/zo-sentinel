from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Mapping, Optional, Tuple, Union

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session

try:
    from .logic import get_stale_scanned_servers as _query_stale_scanned_servers
except ImportError:
    try:
        from .logic import fetch_stale_scanned_servers as _query_stale_scanned_servers
    except ImportError:
        from .logic import get_stale_servers as _query_stale_scanned_servers


router = APIRouter(prefix="/api")


class StaleScannedServer(BaseModel):
    server_id: str
    name: Optional[str] = None
    last_scanned: Optional[str] = None
    scan_count: int = 0
    risk_tier: Optional[str] = None
    registry_source: Optional[str] = None


class StaleScannedResponse(BaseModel):
    cutoff: str
    stale_count: int
    never_scanned_count: int
    servers: List[StaleScannedServer]


def _stringify_timestamp(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def _server_row(value: Any) -> Dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)

    return {
        "server_id": getattr(value, "server_id", None),
        "name": getattr(value, "name", None),
        "last_scanned": getattr(value, "last_scanned", None),
        "scan_count": getattr(value, "scan_count", None),
        "risk_tier": getattr(value, "risk_tier", None),
        "registry_source": getattr(value, "registry_source", None),
    }


def _normalize_result(
    result: Any,
    cutoff: datetime,
) -> Tuple[str, int, int, List[Dict[str, Any]]]:
    if isinstance(result, Mapping):
        raw_servers = result.get("servers") or []
        raw_cutoff = result.get("cutoff", cutoff)
        stale_count = result.get("stale_count")
        never_scanned_count = result.get("never_scanned_count")
    elif isinstance(result, tuple) and len(result) == 4:
        raw_cutoff, stale_count, never_scanned_count, raw_servers = result
    elif isinstance(result, tuple) and len(result) == 3:
        raw_servers, stale_count, never_scanned_count = result
        raw_cutoff = cutoff
    else:
        raw_servers = result or []
        raw_cutoff = cutoff
        stale_count = None
        never_scanned_count = None

    servers: List[Dict[str, Any]] = []
    for item in raw_servers:
        row = _server_row(item)
        scanned_at = row.get("last_scanned")
        scan_count = row.get("scan_count")
        servers.append(
            {
                "server_id": str(row.get("server_id")),
                "name": row.get("name"),
                "last_scanned": _stringify_timestamp(scanned_at),
                "scan_count": int(scan_count or 0),
                "risk_tier": row.get("risk_tier"),
                "registry_source": row.get("registry_source"),
            }
        )

    if stale_count is None:
        stale_count = sum(
            1 for server in servers if server["last_scanned"] is not None
        )
    if never_scanned_count is None:
        never_scanned_count = sum(
            1 for server in servers if server["last_scanned"] is None
        )

    return (
        _stringify_timestamp(raw_cutoff) or cutoff.isoformat(),
        int(stale_count),
        int(never_scanned_count),
        servers,
    )


@router.get(
    "/registry/stale-scanned",
    response_model=StaleScannedResponse,
    summary="List registry servers with stale scan timestamps",
)
def list_stale_scanned_servers(
    hours: int = Query(168, description="Staleness window in hours"),
    limit: int = Query(50, ge=1, le=500),
    session: Session = Depends(get_session),
) -> StaleScannedResponse:
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    result = _query_stale_scanned_servers(session, cutoff, limit)
    cutoff_text, stale_count, never_scanned_count, servers = _normalize_result(
        result, cutoff
    )

    return StaleScannedResponse(
        cutoff=cutoff_text,
        stale_count=stale_count,
        never_scanned_count=never_scanned_count,
        servers=servers,
    )


if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    test_app = FastAPI()
    test_app.include_router(router)
    test_app.dependency_overrides[get_session] = lambda: object()

    original_query = _query_stale_scanned_servers
    try:
        _query_stale_scanned_servers = lambda _session, _cutoff, _limit: {
            "cutoff": _cutoff.isoformat(),
            "stale_count": 1,
            "never_scanned_count": 1,
            "servers": [
                {
                    "server_id": "never-scanned",
                    "name": None,
                    "last_scanned": None,
                    "scan_count": 0,
                    "risk_tier": None,
                    "registry_source": None,
                },
                {
                    "server_id": "stale",
                    "name": "Stale server",
                    "last_scanned": (
                        datetime.now(timezone.utc) - timedelta(days=30)
                    ).isoformat(),
                    "scan_count": 1,
                    "risk_tier": "medium",
                    "registry_source": "registry",
                },
            ],
        }

        response = TestClient(test_app).get(
            "/api/registry/stale-scanned",
            params={"hours": 168, "limit": 50},
        )
        response.raise_for_status()
        payload = response.json()
        assert payload["never_scanned_count"] == 1
        assert payload["stale_count"] == 1
        assert "fresh" not in {
            server["server_id"] for server in payload["servers"]
        }
    finally:
        _query_stale_scanned_servers = original_query

    print("PASS")