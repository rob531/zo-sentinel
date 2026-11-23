"""Read-only MCP fingerprint coverage computation and HTTP endpoint."""

from __future__ import annotations

import json
from typing import Any, Dict, Iterable, List, Set
from urllib.request import Request, urlopen

from fastapi import APIRouter, Depends, FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import Column, MetaData, String, Table, create_engine, insert, inspect, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry


router = APIRouter(prefix="/api")

fingerprint_table = Table(
    "mcp_fingerprints",
    MetaData(),
    Column("server_id", String),
)


def _bus_fingerprint_ids() -> Set[str]:
    payload = json.dumps(
        {"sql": "SELECT server_id FROM mcp_fingerprints"}
    ).encode("utf-8")
    request = Request(
        "http://127.0.0.1:8772/query",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urlopen(request, timeout=2) as response:
        body: Any = json.loads(response.read().decode("utf-8"))

    if isinstance(body, list):
        rows = body
    elif isinstance(body, dict):
        rows = body.get("rows") or body.get("results") or body.get("data") or []
    else:
        rows = []

    identifiers: Set[str] = set()
    for row in rows:
        if isinstance(row, dict):
            value = row.get("server_id")
        elif isinstance(row, (list, tuple)) and row:
            value = row[0]
        else:
            value = row
        if value is not None:
            identifiers.add(str(value))
    return identifiers


def _fingerprint_ids(db: Session) -> Set[str]:
    try:
        bind = db.get_bind()
        if inspect(bind).has_table("mcp_fingerprints"):
            rows = db.execute(
                select(fingerprint_table.c.server_id)
            ).scalars().all()
            return {str(value) for value in rows if value is not None}
    except Exception:
        db.rollback()

    try:
        return _bus_fingerprint_ids()
    except Exception:
        return set()


def compute_fingerprint_coverage(db: Session) -> Dict[str, Any]:
    """Compute registry coverage using the real registry and fingerprint tables."""
    rows = db.execute(
        select(McpServerRegistry.server_id, McpServerRegistry.registry_source)
    ).all()

    fingerprint_ids = _fingerprint_ids(db)
    total_servers = len(rows)
    fingerprinted_servers = sum(
        1 for server_id, _ in rows if str(server_id) in fingerprint_ids
    )

    by_source: Dict[str, Dict[str, int]] = {}
    for server_id, source in rows:
        source_name = source or "unknown"
        bucket = by_source.setdefault(source_name, {"total": 0, "covered": 0})
        bucket["total"] += 1
        if str(server_id) in fingerprint_ids:
            bucket["covered"] += 1

    def ratio(covered: int, total: int) -> float:
        return round(covered / total, 4) if total else 0.0

    by_registry_source: List[Dict[str, Any]] = [
        {
            "source": source,
            "total": values["total"],
            "covered": values["covered"],
            "ratio": ratio(values["covered"], values["total"]),
        }
        for source, values in sorted(by_source.items())
    ]

    return {
        "total_servers": total_servers,
        "fingerprinted_servers": fingerprinted_servers,
        "unfingerprinted_servers": total_servers - fingerprinted_servers,
        "coverage_ratio": ratio(fingerprinted_servers, total_servers),
        "by_registry_source": by_registry_source,
    }


@router.get("/fingerprints/coverage")
def fingerprints_coverage(
    db: Session = Depends(get_session),
) -> Dict[str, Any]:
    return compute_fingerprint_coverage(db)


app = FastAPI()
app.include_router(router)


if __name__ == "__main__":
    from sqlalchemy.pool import StaticPool

    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    McpServerRegistry.__table__.create(bind=test_engine)
    fingerprint_table.create(bind=test_engine)

    with Session(test_engine) as session:
        session.add_all(
            [
                McpServerRegistry(server_id="server-1", registry_source="seeded"),
                McpServerRegistry(server_id="server-2", registry_source="seeded"),
                McpServerRegistry(server_id="server-3", registry_source="seeded"),
            ]
        )
        session.execute(insert(fingerprint_table).values(server_id="server-1"))
        session.execute(insert(fingerprint_table).values(server_id="server-2"))
        session.commit()

    def override_get_session():
        with Session(test_engine) as session:
            yield session

    that_app = FastAPI()
    that_app.include_router(router)
    that_app.dependency_overrides[get_session] = override_get_session

    with TestClient(that_app) as client:
        response = client.get("/api/fingerprints/coverage")
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["coverage_ratio"] == 0.6667, payload
        assert payload["total_servers"] == 3, payload
        assert payload["fingerprinted_servers"] == 2, payload
        assert payload["unfingerprinted_servers"] == 1, payload
        assert any(
            item["source"] == "seeded" and item["total"] == 3 and item["covered"] == 2
            for item in payload["by_registry_source"]
        ), payload

    print("PASS")