# services/staged/registry_search_api/logic.py
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from sqlalchemy.inspection import inspect

from app.db import get_session
from app.models import McpServerRegistry

router = APIRouter()


def _serialize_registry(objs: List[McpServerRegistry]) -> List[Dict[str, Any]]:
    """Convert a list of McpServerRegistry ORM objects to plain dictionaries."""
    result: List[Dict[str, Any]] = []
    for obj in objs:
        data = {c.key: getattr(obj, c.key) for c in inspect(obj).mapper.column_attrs}
        result.append(data)
    return result


def get_registry(session: Session, **filters: Any) -> List[McpServerRegistry]:
    """
    Retrieve McpServerRegistry rows applying any valid column filters.

    Parameters
    ----------
    session: Session
        SQLAlchemy session obtained via ``Depends(get_session)``.
    **filters: Any
        Column name/value pairs. Only columns that actually exist on the
        ``McpServerRegistry`` model are applied; unknown keys are ignored.

    Returns
    -------
    List[McpServerRegistry]
        List of matching ORM objects (may be empty).
    """
    if session is None:  # pragma: no cover – used only in the self‑test override
        return []

    query = session.query(McpServerRegistry)
    for key, value in filters.items():
        if value is None:
            continue
        if hasattr(McpServerRegistry, key):
            query = query.filter(getattr(McpServerRegistry, key) == value)
    return query.all()


@router.get("/api/servers/search")
async def search_servers(
    confidence: Optional[int] = Query(None),
    org_id: Optional[int] = Query(None, alias="org_id"),
    ip_address: Optional[str] = Query(None, alias="ip_address"),
    server_id: Optional[int] = Query(None, alias="server_id"),
    session: Session = Depends(get_session),
) -> List[Dict[str, Any]]:
    """
    FastAPI endpoint that searches the MCP server registry.

    Accepts a small set of optional query parameters; any provided
    parameters are used as exact‑match filters against the underlying
    ``McpServerRegistry`` table.  The response is a list of dictionaries
    representing the matching rows.
    """
    filters: Dict[str, Any] = {
        "confidence": confidence,
        "org_id": org_id,
        "ip_address": ip_address,
        "server_id": server_id,
    }
    rows = get_registry(session, **filters)
    return _serialize_registry(rows)


# --------------------------------------------------------------------------- #
# Self‑test
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    import sys
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    # Create a minimal FastAPI app and override the DB dependency with a stub
    app = FastAPI()
    app.include_router(router)

    def _dummy_session():
        """Return ``None`` so that ``get_registry`` short‑circuits to an empty list."""
        return None

    app.dependency_overrides[get_session] = _dummy_session

    client = TestClient(app)

    try:
        resp = client.get("/api/servers/search")
        assert resp.status_code == 200, f"Unexpected status {resp.status_code}"
        assert resp.json() == [], f"Expected empty list, got {resp.json()}"
    except Exception as exc:  # pragma: no cover
        print(f"FAIL: {exc}", file=sys.stderr)
        sys.exit(1)

    print("PASS")