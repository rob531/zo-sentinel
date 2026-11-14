from __future__ import annotations

import json
import math
import os
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from typing import Any, Iterable
from urllib.error import URLError
from urllib.request import Request, urlopen
from uuid import UUID

from fastapi import APIRouter, Depends, FastAPI, Query
from sqlalchemy import inspect
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry


TIER_ORDER = ("low", "medium", "high", "critical", "unknown")
TIER_ALIASES = {
    "low": "low",
    "lower": "low",
    "minimal": "low",
    "safe": "low",
    "green": "low",
    "tier_3": "low",
    "tier3": "low",
    "tier_4": "low",
    "tier4": "low",
    "medium": "medium",
    "moderate": "medium",
    "amber": "medium",
    "yellow": "medium",
    "tier_2": "medium",
    "tier2": "medium",
    "high": "high",
    "elevated": "high",
    "orange": "high",
    "tier_1": "high",
    "tier1": "high",
    "critical": "critical",
    "severe": "critical",
    "extreme": "critical",
    "red": "critical",
    "tier_0": "critical",
    "tier0": "critical",
}

router = APIRouter()


def _columns(model: type) -> set[str]:
    return set(inspect(model).columns.keys())


def _first_value(row: Any, names: Iterable[str]) -> Any:
    for name in names:
        if hasattr(row, name):
            value = getattr(row, name)
            if value is not None:
                return value
    return None


def _scalar(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, Enum):
        return _scalar(value.value)
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    return number


def _normalized_score(value: Any) -> float | None:
    number = _number(value)
    if number is None:
        return None
    if 0.0 <= number <= 1.0:
        number *= 100.0
    return max(0.0, min(100.0, number))


def _normalize_tier(value: Any) -> str | None:
    if value is None:
        return None
    text = str(_scalar(value)).strip().lower().replace("-", "_").replace(" ", "_")
    if not text:
        return None
    if text in TIER_ALIASES:
        return TIER_ALIASES[text]
    if text in {"tier1", "tier2", "tier3", "tier4"}:
        return TIER_ALIASES[f"tier_{text[-1]}"]
    if text.isdigit():
        tier_number = int(text)
        if 0 <= tier_number <= 3:
            return TIER_ALIASES[f"tier_{tier_number}"]
    return None


def _tier_from_score(score: float | None) -> str:
    if score is None:
        return "unknown"
    if score >= 80.0:
        return "critical"
    if score >= 60.0:
        return "high"
    if score >= 30.0:
        return "medium"
    return "low"


def _query_rows(session: Session, model: type) -> list[Any]:
    try:
        return list(session.query(model).all())
    except SQLAlchemyError:
        return []


def _mesh_rows() -> list[dict[str, Any]]:
    endpoint = os.getenv("WRITE_SERVICE_URL", "http://127.0.0.1:8772").rstrip("/") + "/query"
    payload = json.dumps(
        {
            "sql": "SELECT * FROM mcp_signal_scores",
            "params": [],
        }
    ).encode("utf-8")
    request = Request(
        endpoint,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=1.5) as response:
            body = json.loads(response.read().decode("utf-8"))
    except (OSError, URLError, TimeoutError, ValueError, json.JSONDecodeError):
        return []

    if isinstance(body, list):
        return [row for row in body if isinstance(row, dict)]
    if not isinstance(body, dict):
        return []

    rows = body.get("rows")
    if isinstance(rows, list):
        if rows and all(isinstance(row, dict) for row in rows):
            return rows
        columns = body.get("columns")
        if isinstance(columns, list):
            converted: list[dict[str, Any]] = []
            for row in rows:
                if isinstance(row, list):
                    converted.append(
                        {
                            str(column): row[index] if index < len(row) else None
                            for index, column in enumerate(columns)
                        }
                    )
            return converted

    for key in ("result", "data", "results"):
        value = body.get(key)
        if isinstance(value, list):
            return [row for row in value if isinstance(row, dict)]
        if isinstance(value, dict):
            nested = value.get("rows")
            if isinstance(nested, list):
                return [row for row in nested if isinstance(row, dict)]
    return []


def _mesh_value(row: dict[str, Any], names: Iterable[str]) -> Any:
    for name in names:
        if name in row and row[name] is not None:
            return row[name]
    return None


def _registry_identity(row: Any) -> tuple[str | None, str | None]:
    registry_id = _first_value(
        row,
        ("server_id", "registry_id", "mcp_server_registry_id", "id"),
    )
    name = _first_value(
        row,
        ("server_name", "name", "package_name", "registry_name", "slug"),
    )
    return (
        str(_scalar(registry_id)) if registry_id is not None else None,
        str(_scalar(name)) if name is not None else None,
    )


def _axis_identity(row: Any) -> str | None:
    value = _first_value(
        row,
        (
            "server_id",
            "registry_id",
            "mcp_server_registry_id",
            "mcp_server_id",
            "server_registry_id",
        ),
    )
    return str(_scalar(value)) if value is not None else None


@router.get("/registry-risk-tier-heatmap")
@router.get("/api/registry-risk-tier-heatmap")
@router.get("/api/registry/risk-tier-heatmap")
@router.get("/registry/risk-tier-heatmap")
def registry_risk_tier_heatmap(
    include_mesh: bool = Query(default=True),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    registry_rows = _query_rows(session, McpServerRegistry)
    axis_rows = _query_rows(session, McpLlmAxisScore)
    mesh_rows = _mesh_rows() if include_mesh else []

    mesh_by_id: dict[str, dict[str, Any]] = {}
    mesh_by_name: dict[str, dict[str, Any]] = {}
    for row in mesh_rows:
        server_id = _mesh_value(
            row,
            ("server_id", "registry_id", "mcp_server_registry_id", "mcp_server_id"),
        )
        server_name = _mesh_value(
            row,
            ("server_name", "name", "package_name", "registry_name"),
        )
        if server_id is not None:
            mesh_by_id[str(server_id)] = row
        if server_name is not None:
            mesh_by_name[str(server_name)] = row

    axis_by_id: dict[str, list[Any]] = {}
    for row in axis_rows:
        identity = _axis_identity(row)
        if identity is not None:
            axis_by_id.setdefault(identity, []).append(row)

    details: list[dict[str, Any]] = []
    for row in registry_rows:
        registry_id, server_name = _registry_identity(row)
        explicit_tier = _normalize_tier(
            _first_value(row, ("risk_tier", "tier", "risk_level"))
        )
        registry_score = _normalized_score(
            _first_value(
                row,
                (
                    "risk_score",
                    "risk_score_value",
                    "overall_risk_score",
                    "security_risk_score",
                    "trust_score",
                    "score",
                ),
            )
        )

        linked_axes = axis_by_id.get(registry_id or "", [])
        axis_details: list[dict[str, Any]] = []
        axis_scores: list[float] = []
        for axis_row in linked_axes:
            axis_name = _first_value(
                axis_row,
                ("axis", "axis_name", "dimension", "metric", "name"),
            )
            raw_score = _first_value(
                axis_row,
                (
                    "risk_score",
                    "axis_score",
                    "overall_score",
                    "normalized_score",
                    "score",
                    "value",
                ),
            )
            score = _normalized_score(raw_score)
            if score is not None:
                axis_scores.append(score)
            axis_details.append(
                {
                    "axis": _scalar(axis_name) if axis_name is not None else None,
                    "score": score,
                }
            )

        average_axis_score = (
            sum(axis_scores) / len(axis_scores) if axis_scores else None
        )
        mesh_row = mesh_by_id.get(registry_id or "")
        if mesh_row is None and server_name is not None:
            mesh_row = mesh_by_name.get(server_name)

        mesh_tier = None
        mesh_score = None
        if mesh_row is not None:
            mesh_tier = _normalize_tier(
                _mesh_value(
                    mesh_row,
                    ("risk_tier", "tier", "risk_level", "severity"),
                )
            )
            mesh_score = _normalized_score(
                _mesh_value(
                    mesh_row,
                    (
                        "risk_score",
                        "risk_score_value",
                        "overall_risk_score",
                        "normalized_score",
                        "score",
                    ),
                )
            )

        risk_score = registry_score
        if risk_score is None:
            risk_score = average_axis_score
        if risk_score is None:
            risk_score = mesh_score

        risk_tier = explicit_tier or mesh_tier or _tier_from_score(risk_score)
        details.append(
            {
                "server_id": registry_id,
                "server_name": server_name,
                "risk_tier": risk_tier,
                "risk_score": risk_score,
                "axes": axis_details,
            }
        )

    details.sort(
        key=lambda item: (
            TIER_ORDER.index(item["risk_tier"])
            if item["risk_tier"] in TIER_ORDER
            else len(TIER_ORDER),
            str(item["server_name"] or item["server_id"] or ""),
        )
    )

    tiers: dict[str, list[dict[str, Any]]] = {tier: [] for tier in TIER_ORDER}
    for item in details:
        tiers[item["risk_tier"]].append(item)

    tier_counts = {tier: len(tiers[tier]) for tier in TIER_ORDER}
    total = len(details)

    return {
        "total": total,
        "total_servers": total,
        "tier_counts": tier_counts,
        "tiers": tiers,
        "heatmap": tiers,
        "servers": details,
        "sources": {
            "registry": "app.db:mcp_server_registry",
            "axis_scores": "app.db:mcp_llm_axis_scores",
            "signal_scores": "write_service:mcp_signal_scores" if include_mesh else None,
        },
    }


@router.get("/health")
def health(session: Session = Depends(get_session)) -> dict[str, Any]:
    count = session.query(McpServerRegistry).count()
    return {"status": "ok", "registry_servers": count}


app = FastAPI(
    title="Registry Risk Tier Heatmap",
    version="1.0.0",
)
app.include_router(router)


def _run_self_test() -> None:
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    McpServerRegistry.__table__.create(engine, checkfirst=True)
    try:
        McpLlmAxisScore.__table__.create(engine, checkfirst=True)
    except SQLAlchemyError:
        pass

    testing_session = sessionmaker(bind=engine, expire_on_commit=False)

    def override_get_session():
        db = testing_session()
        try:
            yield db
        finally:
            db.close()

    that_app = FastAPI()
    that_app.include_router(router)
    that_app.dependency_overrides[get_session] = override_get_session

    with TestClient(that_app) as client:
        response = client.get(
            "/registry-risk-tier-heatmap",
            params={"include_mesh": "false"},
        )
        if response.status_code != 200:
            raise AssertionError(
                f"registry-risk-tier-heatmap returned {response.status_code}"
            )
        payload = response.json()
        for key in ("total", "tier_counts", "tiers", "heatmap", "servers"):
            if key not in payload:
                raise AssertionError(f"missing response key: {key}")
        if payload["total"] != len(payload["servers"]):
            raise AssertionError("heat-map totals are inconsistent")
        if sum(payload["tier_counts"].values()) != payload["total"]:
            raise AssertionError("heat-map tier counts are inconsistent")

        health_response = client.get("/health")
        if health_response.status_code != 200:
            raise AssertionError(
                f"health returned {health_response.status_code}"
            )

    engine.dispose()
    print("PASS")
    raise SystemExit(0)


if __name__ == "__main__":
    _run_self_test()