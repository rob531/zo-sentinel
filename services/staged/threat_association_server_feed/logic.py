from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple
from urllib.request import Request, urlopen

from fastapi import Depends, FastAPI
from sqlalchemy import Column, MetaData, String, Table, and_, create_engine, func, or_, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import McpServerRegistry, ThreatIntelRef


_WRITE_SERVICE_URL = "http://127.0.0.1:8772/query"
_INDICATOR_FIELDS = ("indicator_type", "indicator_value")


def _normalise_rows(payload: Any) -> List[Mapping[str, Any]]:
    if isinstance(payload, list):
        return payload
    if not isinstance(payload, Mapping):
        return []

    rows = payload.get("rows")
    if rows is None:
        rows = payload.get("data")
    if rows is None:
        rows = payload.get("results")
    if rows is None and isinstance(payload.get("result"), (list, Mapping)):
        return _normalise_rows(payload["result"])
    if not isinstance(rows, list):
        return []

    columns = payload.get("columns")
    normalised: List[Mapping[str, Any]] = []
    for row in rows:
        if isinstance(row, Mapping):
            normalised.append(row)
        elif isinstance(row, Sequence) and not isinstance(row, (str, bytes)):
            if isinstance(columns, list):
                normalised.append(dict(zip(columns, row)))
            else:
                normalised.append({})
    return normalised


def _extract_total(payload: Any) -> Optional[int]:
    if isinstance(payload, Mapping):
        for key in ("total", "count"):
            value = payload.get(key)
            if value is not None:
                try:
                    return int(value)
                except (TypeError, ValueError):
                    pass

        for key in ("result", "data"):
            if key in payload:
                total = _extract_total(payload[key])
                if total is not None:
                    return total

        rows = _normalise_rows(payload)
        if len(rows) == 1 and isinstance(rows[0], Mapping):
            for key in ("total", "count"):
                if key in rows[0]:
                    try:
                        return int(rows[0][key])
                    except (TypeError, ValueError):
                        return None
    return None


def _write_service_query(sql: str, params: Sequence[Any]) -> Any:
    body = json.dumps({"sql": sql, "params": list(params)}).encode("utf-8")
    request = Request(
        _WRITE_SERVICE_URL,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urlopen(request, timeout=5) as response:
        return json.loads(response.read().decode("utf-8"))


def _load_bus_associations(
    server_id: Optional[str], limit: int
) -> Tuple[int, List[Mapping[str, Any]]]:
    where = " WHERE server_id = ?" if server_id is not None else ""
    count_payload = _write_service_query(
        f"SELECT COUNT(*) AS total FROM mcp_threat_associations{where}",
        (server_id,) if server_id is not None else (),
    )
    total = _extract_total(count_payload)
    if total is None:
        raise RuntimeError("write service did not return an association count")

    rows_payload = _write_service_query(
        "SELECT server_id, indicator_type, indicator_value "
        "FROM mcp_threat_associations"
        f"{where} ORDER BY server_id, indicator_type, indicator_value LIMIT ?",
        ((server_id, limit) if server_id is not None else (limit,)),
    )
    return total, _normalise_rows(rows_payload)


def _load_sqlite_associations(
    db: Session, server_id: Optional[str], limit: int
) -> Tuple[int, List[Mapping[str, Any]]]:
    bind = db.get_bind()
    table = Table("mcp_threat_associations", MetaData(), autoload_with=bind)
    available = {column.name for column in table.columns}
    fields = [
        field
        for field in ("server_id", "indicator_type", "indicator_value")
        if field in available
    ]
    if "server_id" not in fields:
        raise RuntimeError("mcp_threat_associations.server_id is missing")

    query = select(*(table.c[field] for field in fields))
    count_query = select(func.count()).select_from(table)
    if server_id is not None:
        query = query.where(table.c.server_id == server_id)
        count_query = count_query.where(table.c.server_id == server_id)

    total = int(db.execute(count_query).scalar_one())
    query = query.order_by(
        table.c.server_id,
        table.c.indicator_type if "indicator_type" in available else table.c.server_id,
        table.c.indicator_value if "indicator_value" in available else table.c.server_id,
    )
    if limit >= 0:
        query = query.limit(limit)

    rows = [dict(row._mapping) for row in db.execute(query).all()]
    return total, rows


def _load_associations(
    db: Session, server_id: Optional[str], limit: int
) -> Tuple[int, List[Mapping[str, Any]]]:
    dialect = db.get_bind().dialect.name
    if dialect == "sqlite":
        return _load_sqlite_associations(db, server_id, limit)
    return _load_bus_associations(server_id, limit)


def _registry_by_server(
    db: Session, server_ids: Iterable[str]
) -> Dict[str, McpServerRegistry]:
    identifiers = sorted({server_id for server_id in server_ids if server_id})
    if not identifiers:
        return {}

    rows = db.execute(
        select(McpServerRegistry).where(McpServerRegistry.server_id.in_(identifiers))
    ).scalars()
    return {row.server_id: row for row in rows}


def _refs_by_indicator(
    db: Session, pairs: Iterable[Tuple[str, str]]
) -> Dict[Tuple[str, str], ThreatIntelRef]:
    unique_pairs = sorted({pair for pair in pairs if pair[0] and pair[1]})
    if not unique_pairs:
        return {}

    clauses = [
        and_(
            ThreatIntelRef.indicator_type == indicator_type,
            ThreatIntelRef.indicator_value == indicator_value,
        )
        for indicator_type, indicator_value in unique_pairs
    ]
    rows = db.execute(
        select(ThreatIntelRef)
        .where(or_(*clauses))
        .order_by(ThreatIntelRef.id)
    ).scalars()

    refs: Dict[Tuple[str, str], ThreatIntelRef] = {}
    for row in rows:
        refs.setdefault((row.indicator_type, row.indicator_value), row)
    return refs


def get_threat_associations(
    db: Session,
    server_id: Optional[str] = None,
    limit: int = 100,
) -> Dict[str, Any]:
    """Return threat associations enriched from the authoritative app tables."""
    limit = int(limit)
    if limit < 0:
        raise ValueError("limit must not be negative")

    total, associations = _load_associations(db, server_id, limit)
    servers = _registry_by_server(
        db, (str(row.get("server_id") or "") for row in associations)
    )
    refs = _refs_by_indicator(
        db,
        (
            (
                str(row.get("indicator_type") or ""),
                str(row.get("indicator_value") or ""),
            )
            for row in associations
        ),
    )

    items: List[Dict[str, Any]] = []
    for association in associations:
        association_server_id = str(association.get("server_id") or "")
        indicator_type = str(association.get("indicator_type") or "")
        indicator_value = str(association.get("indicator_value") or "")
        server = servers.get(association_server_id)
        ref = refs.get((indicator_type, indicator_value))

        items.append(
            {
                "server_id": association_server_id,
                "server_name": server.name if server is not None else None,
                "risk_tier": server.risk_tier if server is not None else None,
                "indicator_type": indicator_type,
                "indicator_value": indicator_value,
                "pulse_id": ref.pulse_id if ref is not None else None,
                "pulse_name": ref.pulse_name if ref is not None else None,
                "source": ref.source if ref is not None else None,
                "source_url": ref.source_url if ref is not None else None,
                "fetched_at": ref.fetched_at if ref is not None else None,
            }
        )

    return {"total": int(total), "items": items}


def get_threat_association_feed(
    db: Session,
    server_id: Optional[str] = None,
    limit: int = 100,
) -> Dict[str, Any]:
    return get_threat_associations(db, server_id=server_id, limit=limit)


def list_threat_associations(
    db: Session,
    server_id: Optional[str] = None,
    limit: int = 100,
) -> Dict[str, Any]:
    return get_threat_associations(db, server_id=server_id, limit=limit)


def _self_test() -> None:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    McpServerRegistry.__table__.create(engine)
    ThreatIntelRef.__table__.create(engine)

    association_metadata = MetaData()
    association_table = Table(
        "mcp_threat_associations",
        association_metadata,
        Column("server_id", String, primary_key=True),
        Column("indicator_type", String, nullable=False),
        Column("indicator_value", String, nullable=False),
    )
    association_metadata.create_all(engine)

    with Session(engine) as db:
        db.add_all(
            [
                McpServerRegistry(
                    server_id="server-1",
                    name="Primary Server",
                    risk_tier="high",
                    verdict="review",
                ),
                McpServerRegistry(
                    server_id="server-2",
                    name="Secondary Server",
                    risk_tier="medium",
                    verdict="approved",
                ),
                ThreatIntelRef(
                    id=1,
                    indicator_type="domain",
                    indicator_value="example.invalid",
                    pulse_id="pulse-1",
                    pulse_name="Example Pulse",
                    source="fixture",
                    source_url="https://intel.example/pulse-1",
                    fetched_at=datetime.now(timezone.utc),
                ),
            ]
        )
        db.execute(
            association_table.insert(),
            [
                {
                    "server_id": "server-1",
                    "indicator_type": "domain",
                    "indicator_value": "example.invalid",
                },
                {
                    "server_id": "server-2",
                    "indicator_type": "domain",
                    "indicator_value": "example.invalid",
                },
            ],
        )
        db.commit()

        local_app = FastAPI()

        @local_app.get("/api/threat/associations")
        def associations_route(
            server_id: Optional[str] = None,
            limit: int = 100,
            session: Session = Depends(get_session),
        ) -> Dict[str, Any]:
            return get_threat_associations(
                session, server_id=server_id, limit=limit
            )

        def override_session():
            yield db

        local_app.dependency_overrides[get_session] = override_session

        from fastapi.testclient import TestClient

        with TestClient(local_app) as client:
            response = client.get("/api/threat/associations")
            assert response.status_code == 200
            payload = response.json()
            assert payload["total"] == 2
            assert len(payload["items"]) == 2
            assert all(item["server_id"] for item in payload["items"])
            assert all(item["pulse_id"] for item in payload["items"])

            filtered = client.get(
                "/api/threat/associations",
                params={"server_id": "server-1"},
            )
            assert filtered.status_code == 200
            filtered_payload = filtered.json()
            assert filtered_payload["total"] == 1
            assert len(filtered_payload["items"]) == 1
            assert filtered_payload["items"][0]["server_id"] == "server-1"

    print("PASS")


if __name__ == "__main__":
    _self_test()