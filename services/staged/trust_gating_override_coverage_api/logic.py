from __future__ import annotations

import importlib
from datetime import datetime, timezone
from typing import Any

from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry

try:
    import trust_gating_override
except ImportError:
    trust_gating_override = importlib.import_module("app.trust_gating_override")


_MISSING_VALUES = {None, ""}


def _normalized(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        value = value.strip()
        return value.upper() if value else None
    return str(value).strip().upper() or None


def _derived_tier(value: Any) -> str | None:
    """Extract a tier from the different shapes used by trust_gate()."""
    if value in _MISSING_VALUES:
        return None

    if isinstance(value, dict):
        for key in ("derived_tier", "risk_tier", "tier", "verdict"):
            if key in value:
                result = _derived_tier(value[key])
                if result is not None:
                    return result
        for nested_key in ("result", "decision", "gate"):
            if nested_key in value:
                result = _derived_tier(value[nested_key])
                if result is not None:
                    return result
        return None

    if isinstance(value, (tuple, list)):
        for item in value:
            result = _derived_tier(item)
            if result is not None:
                return result
        return None

    return _normalized(value)


def _query_rows(session: Any, model: Any) -> list[Any]:
    query = session.query(model)
    if hasattr(query, "order_by"):
        order_column = getattr(model, "server_id", None)
        if order_column is not None:
            query = query.order_by(order_column)
    return list(query.all())


def get_override_coverage(session: Any, limit: int = 200) -> dict[str, Any]:
    """Compare trust-gate-derived tiers with persisted registry verdicts."""
    try:
        limit = int(limit)
    except (TypeError, ValueError):
        limit = 200
    limit = max(1, min(1000, limit))

    servers = _query_rows(session, McpServerRegistry)[:limit]
    axis_rows = _query_rows(session, McpLlmAxisScore)

    axes_by_server: dict[Any, dict[str, Any]] = {}
    for axis in axis_rows:
        server_id = getattr(axis, "server_id", None)
        axis_name = getattr(axis, "axis_name", None)
        label = getattr(axis, "label", None)
        if server_id is None or axis_name is None:
            continue
        axes_by_server.setdefault(server_id, {})[str(axis_name)] = label

    mismatches: list[dict[str, Any]] = []
    compared = 0
    derived_present = 0

    for server in servers:
        server_id = getattr(server, "server_id", None)
        name = getattr(server, "name", None)
        url = getattr(server, "url", None)
        stored_verdict = getattr(server, "verdict", None)
        stored_risk_tier = getattr(server, "risk_tier", None)

        compared += 1
        try:
            gate_result = trust_gating_override.trust_gate(
                url,
                name,
                axes_by_server.get(server_id, {}),
            )
        except Exception:
            gate_result = None

        derived_tier = _derived_tier(gate_result)
        if derived_tier is not None:
            derived_present += 1

        stored_values = {
            value
            for value in (_normalized(stored_verdict), _normalized(stored_risk_tier))
            if value is not None
        }
        if derived_tier is not None and derived_tier not in stored_values:
            mismatches.append(
                {
                    "server_id": server_id,
                    "name": name,
                    "stored_verdict": stored_verdict,
                    "stored_risk_tier": stored_risk_tier,
                    "derived_tier": derived_tier,
                }
            )

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "total_servers": len(servers),
        "compared": compared,
        "derived_present": derived_present,
        "derived_missing": compared - derived_present,
        "mismatches": mismatches,
    }


# Names used by staged routers and consumers.
get_trust_gating_override_coverage = get_override_coverage
get_override_coverage_data = get_override_coverage
collect_override_coverage = get_override_coverage


if __name__ == "__main__":
    from fastapi import Depends, FastAPI

    class _Rows:
        def __init__(self, rows: list[Any]):
            self.rows = rows

        def order_by(self, *_args: Any) -> "_Rows":
            return self

        def limit(self, _limit: int) -> "_Rows":
            return self

        def all(self) -> list[Any]:
            return list(self.rows)

    class _Session:
        def __init__(self) -> None:
            self.servers = [
                McpServerRegistry(
                    server_id="server-match",
                    name="matching-server",
                    url="https://matching.example",
                    verdict="HIGH",
                    risk_tier="HIGH",
                    confidence=0.9,
                    trust_score=0.9,
                ),
                McpServerRegistry(
                    server_id="server-mismatch",
                    name="mismatch-server",
                    url="https://mismatch.example",
                    verdict="HIGH",
                    risk_tier="HIGH",
                    confidence=0.9,
                    trust_score=0.9,
                ),
            ]
            self.axes = [
                McpLlmAxisScore(
                    server_id="server-match",
                    axis_name="quality",
                    label="GOOD",
                    p_top=0.1,
                    p_critical=0.01,
                    escalated=False,
                ),
                McpLlmAxisScore(
                    server_id="server-mismatch",
                    axis_name="quality",
                    label="CRITICAL",
                    p_top=0.01,
                    p_critical=0.99,
                    escalated=True,
                ),
            ]

        def query(self, model: Any) -> _Rows:
            if model is McpServerRegistry:
                return _Rows(self.servers)
            if model is McpLlmAxisScore:
                return _Rows(self.axes)
            return _Rows([])

    test_session = _Session()
    original_trust_gate = trust_gating_override.trust_gate

    def _test_trust_gate(_url: Any, _name: Any, axes: dict[str, Any]) -> str:
        return "CRITICAL" if any(str(label).upper() == "CRITICAL" for label in axes.values()) else "HIGH"

    trust_gating_override.trust_gate = _test_trust_gate
    test_app = FastAPI()
    test_app.dependency_overrides[get_session] = lambda: test_session

    @test_app.get("/api/trust-gating/override-coverage")
    def override_coverage(session: Any = Depends(get_session)) -> dict[str, Any]:
        return get_override_coverage(session)

    try:
        result = override_coverage(test_session)
        assert result["compared"] == 2
        assert result["derived_missing"] == 0
        assert len(result["mismatches"]) >= 1
    finally:
        trust_gating_override.trust_gate = original_trust_gate

    print("PASS")