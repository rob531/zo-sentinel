from __future__ import annotations

import json
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Mapping
from urllib.request import Request, urlopen

from sqlalchemy import inspect, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry


_ASSOCIATION_TABLE = "mcp_threat_associations"
_BUS_QUERY_URL = "http://127.0.0.1:8772/query"


def _validated_days(days: int) -> int:
    try:
        value = int(days)
    except (TypeError, ValueError) as exc:
        raise ValueError("days must be an integer") from exc
    if value < 1:
        raise ValueError("days must be at least 1")
    return value


def _parse_datetime(value: Any) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    else:
        raise ValueError("invalid associated_at value")
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _bus_association_rows(cutoff: datetime) -> List[Dict[str, Any]]:
    body = json.dumps(
        {
            "sql": (
                "SELECT associated_at, threat_type, severity, server_id, confidence "
                "FROM mcp_threat_associations "
                "WHERE associated_at >= %s"
            ),
            "params": [cutoff.isoformat()],
        }
    ).encode("utf-8")
    request = Request(
        _BUS_QUERY_URL,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urlopen(request, timeout=10) as response:
        payload = json.loads(response.read().decode("utf-8"))

    if isinstance(payload, list):
        rows = payload
    elif isinstance(payload, Mapping):
        candidate = payload.get("rows")
        if candidate is None and isinstance(payload.get("result"), Mapping):
            candidate = payload["result"].get("rows")
        if candidate is None and isinstance(payload.get("data"), list):
            candidate = payload["data"]
        rows = candidate or []
    else:
        rows = []
    return [dict(row) for row in rows if isinstance(row, Mapping)]


def _registry_by_server(db: Session, server_ids: Iterable[str]) -> Dict[str, McpServerRegistry]:
    ids = sorted({server_id for server_id in server_ids if server_id is not None})
    if not ids:
        return {}
    records = db.execute(
        select(McpServerRegistry).where(McpServerRegistry.server_id.in_(ids))
    ).scalars()
    return {record.server_id: record for record in records}


def _association_rows(
    db: Session, cutoff: datetime
) -> List[Dict[str, Any]]:
    bind = db.get_bind()
    if bind is not None and inspect(bind).has_table(_ASSOCIATION_TABLE):
        association = db.get_bind().execute(
            select(
                *_association_column_values(bind)
            )
        ).mappings().all()
        return [dict(row) for row in association]

    return _bus_association_rows(cutoff)


def _association_column_values(bind: Any) -> tuple[Any, ...]:
    from sqlalchemy import Table

    association = Table(_ASSOCIATION_TABLE, McpServerRegistry.metadata, autoload_with=bind)
    return (
        association.c.associated_at,
        association.c.threat_type,
        association.c.severity,
        association.c.server_id,
        association.c.confidence,
    )


def _direct_association_rows(
    db: Session, cutoff: datetime
) -> List[Dict[str, Any]]:
    bind = db.get_bind()
    if bind is None or not inspect(bind).has_table(_ASSOCIATION_TABLE):
        return _bus_association_rows(cutoff)

    from sqlalchemy import Table

    association = Table(_ASSOCIATION_TABLE, McpServerRegistry.metadata, autoload_with=bind)
    statement = (
        select(
            association.c.associated_at,
            association.c.threat_type,
            association.c.severity,
            association.c.server_id,
            association.c.confidence,
            McpServerRegistry.name.label("registry_name"),
            McpServerRegistry.risk_tier.label("registry_risk_tier"),
        )
        .select_from(
            association.outerjoin(
                McpServerRegistry,
                association.c.server_id == McpServerRegistry.server_id,
            )
        )
        .where(association.c.associated_at >= cutoff)
    )
    return [dict(row) for row in db.execute(statement).mappings().all()]


def compute_rollup(db: Session, days: int = 30, top_n: int = 10) -> Dict[str, Any]:
    """Compute threat-association totals and rankings over the requested period."""
    days = _validated_days(days)
    top_n = max(1, int(top_n))
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    rows = _direct_association_rows(db, cutoff)
    registry_ids = [row.get("server_id") for row in rows]
    registry = _registry_by_server(db, registry_ids)

    total_associations = len(rows)
    server_ids = {row.get("server_id") for row in rows if row.get("server_id") is not None}
    distinct_servers = len(server_ids)

    grouped_counts: Counter[tuple[str, str]] = Counter()
    grouped_servers: defaultdict[tuple[str, str], set[str]] = defaultdict(set)
    server_counts: Counter[str] = Counter()

    for row in rows:
        threat_type = str(row.get("threat_type") or "unknown")
        severity = str(row.get("severity") or "unknown")
        key = (threat_type, severity)
        grouped_counts[key] += 1

        server_id = row.get("server_id")
        if server_id is not None:
            server_id = str(server_id)
            grouped_servers[key].add(server_id)
            server_counts[server_id] += 1

    by_threat_type: List[Dict[str, Any]] = []
    for (threat_type, severity), count in grouped_counts.most_common():
        by_threat_type.append(
            {
                "threat_type": threat_type,
                "severity": severity,
                "count": count,
                "server_count": len(grouped_servers[(threat_type, severity)]),
                "share": count / total_associations if total_associations else 0.0,
            }
        )

    by_threat_type.sort(
        key=lambda item: (-item["count"], item["threat_type"], item["severity"])
    )

    top_servers: List[Dict[str, Any]] = []
    for server_id, count in server_counts.most_common(top_n):
        record = registry.get(server_id)
        top_servers.append(
            {
                "server_id": server_id,
                "name": record.name if record is not None else None,
                "risk_tier": record.risk_tier if record is not None else None,
                "count": count,
            }
        )

    return {
        "days": days,
        "total_associations": total_associations,
        "distinct_servers": distinct_servers,
        "by_threat_type": by_threat_type,
        "top_servers": top_servers,
    }


def get_association_rollup(db: Session, days: int = 30, top_n: int = 10) -> Dict[str, Any]:
    return compute_rollup(db, days=days, top_n=top_n)


def get_threat_association_rollup(
    db: Session, days: int = 30, top_n: int = 10
) -> Dict[str, Any]:
    return compute_rollup(db, days=days, top_n=top_n)


def get_rollup(db: Session, days: int = 30, top_n: int = 10) -> Dict[str, Any]:
    return compute_rollup(db, days=days, top_n=top_n)


if __name__ == "__main__":
    from fastapi import Depends, FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import Column, DateTime, Float, Integer, MetaData, String, Table
    from sqlalchemy.orm import Session as TestSession
    from sqlalchemy.pool import StaticPool
    from sqlalchemy import create_engine

    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    metadata = MetaData()
    registry_table = McpServerRegistry.__table__.to_metadata(metadata)
    association_table = Table(
        "mcp_threat_associations",
        metadata,
        Column("associated_at", DateTime, nullable=False),
        Column("threat_type", String, nullable=False),
        Column("severity", String, nullable=False),
        Column("server_id", String, nullable=True),
        Column("confidence", Float, nullable=True),
    )
    metadata.create_all(engine)

    with TestSession(engine) as session:
        session.add_all(
            [
                McpServerRegistry(server_id="srv-1", name="Alpha", risk_tier="high"),
                McpServerRegistry(server_id="srv-2", name="Beta", risk_tier="medium"),
                McpServerRegistry(server_id="srv-3", name="Gamma", risk_tier="low"),
            ]
        )
        session.commit()

        now = datetime.now(timezone.utc).replace(tzinfo=None)
        session.execute(
            association_table.insert(),
            [
                {
                    "associated_at": now,
                    "threat_type": "phishing",
                    "severity": "high",
                    "server_id": "srv-1",
                    "confidence": 0.95,
                },
                {
                    "associated_at": now - timedelta(days=1),
                    "threat_type": "phishing",
                    "severity": "medium",
                    "server_id": "srv-2",
                    "confidence": 0.80,
                },
                {
                    "associated_at": now,
                    "threat_type": "malware",
                    "severity": "critical",
                    "server_id": "srv-3",
                    "confidence": 0.99,
                },
                {
                    "associated_at": now - timedelta(days=1),
                    "threat_type": "malware",
                    "severity": "high",
                    "server_id": "srv-1",
                    "confidence": 0.90,
                },
                {
                    "associated_at": now - timedelta(hours=12),
                    "threat_type": "phishing",
                    "severity": "high",
                    "server_id": "srv-2",
                    "confidence": 0.75,
                },
            ],
        )
        session.commit()

        local_app = FastAPI()

        @local_app.get("/api/threat/associations/rollup")
        def rollup(days: int = 30, db: TestSession = Depends(get_session)):
            result = compute_rollup(db, days=days)
            return {
                "days": result["days"],
                "total_associations": result["total_associations"],
                "distinct_servers": result["distinct_servers"],
                "by_threat_type": [
                    {
                        "threat_type": item["threat_type"],
                        "severity": item["severity"],
                        "count": item["count"],
                        "server_count": item["server_count"],
                    }
                    for item in result["by_threat_type"]
                ],
                "top_servers": result["top_servers"],
            }

        def override_session():
            with TestSession(engine) as session:
                yield session

        local_app.dependency_overrides[get_session] = override_session
        with TestClient(local_app) as client:
            response = client.get("/api/threat/associations/rollup", params={"days": 30})
            assert response.status_code == 200, response.text
            payload = response.json()
            assert payload["total_associations"] == 5, payload
            assert payload["distinct_servers"] == 3, payload
            assert len(payload["by_threat_type"]) >= 1, payload
            assert payload["top_servers"], payload

    print("PASS")