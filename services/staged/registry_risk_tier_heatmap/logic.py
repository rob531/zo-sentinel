from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Mapping, Optional

from fastapi import APIRouter, Depends
from fastapi import FastAPI
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore, McpScoreDispute, McpServerRegistry


router = APIRouter()

_TIER_ORDER = ("critical", "high", "medium", "low")
_TIER_ALIASES = {
    "crit": "critical",
    "critical": "critical",
    "sev0": "critical",
    "p0": "critical",
    "high": "high",
    "sev1": "high",
    "p1": "high",
    "medium": "medium",
    "med": "medium",
    "moderate": "medium",
    "sev2": "medium",
    "p2": "medium",
    "low": "low",
    "sev3": "low",
    "p3": "low",
    "informational": "low",
    "info": "low",
}


def _first_value(
    row: Mapping[str, Any],
    names: Iterable[str],
    patterns: Iterable[str] = (),
) -> Any:
    for name in names:
        if name in row and row[name] is not None:
            return row[name]
    for pattern in patterns:
        for name, value in row.items():
            lowered = name.lower()
            if pattern in lowered and value is not None:
                return value
    return None


def _as_float(value: Any) -> Optional[float]:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _normalize_tier(value: Any) -> Optional[str]:
    if value is None:
        return None
    normalized = str(value).strip().lower().replace("-", "_").replace(" ", "_")
    if normalized in _TIER_ALIASES:
        return _TIER_ALIASES[normalized]
    for alias, tier in _TIER_ALIASES.items():
        if alias in normalized:
            return tier
    return None


def _score_to_tier(score: Optional[float]) -> str:
    if score is None:
        return "unknown"
    if score >= 75:
        return "critical"
    if score >= 50:
        return "high"
    if score >= 25:
        return "medium"
    return "low"


def _identity(row: Mapping[str, Any]) -> str:
    value = _first_value(
        row,
        (
            "server_id",
            "registry_id",
            "mcp_server_id",
            "server_uuid",
            "uuid",
            "id",
        ),
        ("server_id", "registry_id", "server_key"),
    )
    return str(value) if value is not None else ""


def _server_name(row: Mapping[str, Any], identity: str) -> str:
    value = _first_value(
        row,
        (
            "server_name",
            "name",
            "hostname",
            "display_name",
            "registry_name",
        ),
        ("server_name", "display_name", "hostname"),
    )
    return str(value) if value is not None else identity


def _rows(session: Session, model: Any) -> List[Mapping[str, Any]]:
    return list(session.execute(select(model)).mappings().all())


def _axis_name(row: Mapping[str, Any]) -> str:
    value = _first_value(
        row,
        (
            "axis_name",
            "axis_key",
            "axis",
            "dimension",
            "llm_axis",
            "axis_type",
        ),
        ("axis_name", "axis_key", "dimension", "axis_type"),
    )
    return str(value) if value is not None else "overall"


def _axis_score(row: Mapping[str, Any]) -> Optional[float]:
    value = _first_value(
        row,
        (
            "axis_score",
            "llm_axis_score",
            "score",
            "value",
            "score_value",
            "normalized_score",
        ),
        ("axis_score", "score_value", "normalized_score"),
    )
    return _as_float(value)


def _registry_score(row: Mapping[str, Any]) -> Optional[float]:
    value = _first_value(
        row,
        (
            "risk_score",
            "overall_score",
            "composite_score",
            "total_score",
            "llm_score",
            "score",
        ),
        ("risk_score", "overall_score", "composite_score", "total_score"),
    )
    return _as_float(value)


def _registry_tier(row: Mapping[str, Any]) -> Optional[str]:
    value = _first_value(
        row,
        (
            "risk_tier",
            "llm_risk_tier",
            "risk_level",
            "tier",
            "verdict",
        ),
        ("risk_tier", "risk_level"),
    )
    return _normalize_tier(value)


def _dispute_status(row: Mapping[str, Any]) -> str:
    value = _first_value(
        row,
        ("status", "dispute_status", "state", "resolution"),
        ("dispute_status", "resolution_status"),
    )
    return str(value).strip().lower() if value is not None else "unknown"


def _build_heatmap(
    registry_rows: List[Mapping[str, Any]],
    score_rows: List[Mapping[str, Any]],
    dispute_rows: List[Mapping[str, Any]],
) -> Dict[str, Any]:
    axes_by_server: Dict[str, Dict[str, float]] = {}
    for row in score_rows:
        identity = _identity(row)
        axis = _axis_name(row)
        score = _axis_score(row)
        if identity and score is not None:
            axes_by_server.setdefault(identity, {})[axis] = score

    disputes_by_server: Dict[str, List[Dict[str, Any]]] = {}
    for row in dispute_rows:
        identity = _identity(row)
        if not identity:
            continue
        disputes_by_server.setdefault(identity, []).append(
            {
                "id": _first_value(row, ("id", "dispute_id"), ("dispute_id",)),
                "status": _dispute_status(row),
                "axis": _first_value(
                    row,
                    ("axis_name", "axis_key", "axis"),
                    ("axis_name", "axis_key"),
                ),
                "score": _as_float(
                    _first_value(
                        row,
                        ("score", "disputed_score", "proposed_score", "value"),
                        ("disputed_score", "proposed_score"),
                    )
                ),
            }
        )

    servers: List[Dict[str, Any]] = []
    for row in registry_rows:
        identity = _identity(row)
        if not identity:
            continue
        axis_scores = axes_by_server.get(identity, {})
        score = _registry_score(row)
        if score is None and axis_scores:
            score = sum(axis_scores.values()) / len(axis_scores)
        tier = _registry_tier(row) or _score_to_tier(score)
        servers.append(
            {
                "server_id": identity,
                "server_name": _server_name(row, identity),
                "risk_tier": tier,
                "risk_score": score,
                "axis_scores": axis_scores,
                "disputes": disputes_by_server.get(identity, []),
                "dispute_count": len(disputes_by_server.get(identity, [])),
            }
        )

    servers.sort(
        key=lambda item: (
            _TIER_ORDER.index(item["risk_tier"])
            if item["risk_tier"] in _TIER_ORDER
            else len(_TIER_ORDER),
            -(item["risk_score"] if item["risk_score"] is not None else -1),
            item["server_name"].lower(),
        )
    )

    tier_summary: Dict[str, Dict[str, Any]] = {
        tier: {"count": 0, "server_ids": [], "servers": []}
        for tier in _TIER_ORDER
    }
    tier_summary["unknown"] = {"count": 0, "server_ids": [], "servers": []}
    for server in servers:
        summary = tier_summary.setdefault(
            server["risk_tier"], {"count": 0, "server_ids": [], "servers": []}
        )
        summary["count"] += 1
        summary["server_ids"].append(server["server_id"])
        summary["servers"].append(server["server_name"])

    axis_names = sorted(
        {
            axis
            for server in servers
            for axis in server["axis_scores"].keys()
        }
    )
    heatmap = [
        {
            "server_id": server["server_id"],
            "server_name": server["server_name"],
            "risk_tier": server["risk_tier"],
            "risk_score": server["risk_score"],
            "scores": {
                axis: server["axis_scores"].get(axis) for axis in axis_names
            },
            "axis_scores": server["axis_scores"],
        }
        for server in servers
    ]

    return {
        "heatmap": heatmap,
        "servers": servers,
        "tiers": tier_summary,
        "summary": {
            "total_servers": len(servers),
            "total_disputes": sum(
                server["dispute_count"] for server in servers
            ),
            "tier_counts": {
                tier: tier_summary.get(tier, {}).get("count", 0)
                for tier in (*_TIER_ORDER, "unknown")
            },
        },
        "axes": axis_names,
        "total_servers": len(servers),
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


def get_registry_risk_tier_heatmap(
    session: Session = Depends(get_session),
) -> Dict[str, Any]:
    registry_rows = _rows(session, McpServerRegistry)
    score_rows = _rows(session, McpLlmAxisScore)
    dispute_rows = _rows(session, McpScoreDispute)
    return _build_heatmap(registry_rows, score_rows, dispute_rows)


def get_registry_risk_heatmap(
    session: Session = Depends(get_session),
) -> Dict[str, Any]:
    return get_registry_risk_tier_heatmap(session)


def build_registry_risk_tier_heatmap(
    session: Session = Depends(get_session),
) -> Dict[str, Any]:
    return get_registry_risk_tier_heatmap(session)


router.add_api_route(
    "/registry/risk-tier-heatmap",
    get_registry_risk_tier_heatmap,
    methods=["GET"],
    name="get_registry_risk_tier_heatmap",
)
router.add_api_route(
    "/api/registry/risk-tier-heatmap",
    get_registry_risk_tier_heatmap,
    methods=["GET"],
    name="get_registry_risk_tier_heatmap_api",
)

app = FastAPI(title="Registry Risk Tier Heatmap")
app.include_router(router)


if __name__ == "__main__":
    from fastapi.testclient import TestClient

    class _SelfTestResult:
        def __init__(self, rows):
            self._rows = rows

        def mappings(self):
            return self

        def all(self):
            return self._rows

    class _SelfTestSession:
        def execute(self, statement):
            entity = statement.column_descriptions[0].get("entity")
            if entity is McpServerRegistry:
                rows = [
                    {"server_id": "self-test", "server_name": "self-test"}
                ]
            elif entity is McpLlmAxisScore:
                rows = [
                    {"server_id": "self-test", "axis_name": "safety", "score": 91}
                ]
            elif entity is McpScoreDispute:
                rows = []
            else:
                rows = []
            return _SelfTestResult(rows)

    that_app = FastAPI()
    that_app.include_router(router)
    that_app.dependency_overrides[get_session] = lambda: _SelfTestSession()
    response = TestClient(that_app).get("/registry/risk-tier-heatmap")
    payload = response.json()
    assert response.status_code == 200
    assert payload["total_servers"] == 1
    assert payload["servers"][0]["risk_tier"] == "critical"
    print("PASS")