"""Derived trust-gate tier API logic."""

from collections.abc import Mapping, Sequence
from typing import Any

import trust_gating_override
from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry
from fastapi import Depends
from sqlalchemy import select
from sqlalchemy.orm import Session


_TRUSTED_VALUES = {
    "trusted",
    "trust",
    "approved",
    "approve",
    "allowed",
    "allow",
    "pass",
    "passed",
    "safe",
    "true",
    "yes",
    "1",
}
_UNTRUSTED_VALUES = {
    "untrusted",
    "untrust",
    "not_trusted",
    "not-trusted",
    "rejected",
    "reject",
    "blocked",
    "block",
    "denied",
    "deny",
    "fail",
    "failed",
    "unsafe",
    "false",
    "no",
    "0",
}


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        normalized = value.strip().lower().replace(" ", "_")
        if normalized in _TRUSTED_VALUES:
            return True
        if normalized in _UNTRUSTED_VALUES:
            return False
    return bool(value)


def _verdict_to_bool(value: Any) -> bool:
    return _as_bool(value)


def _risk_value(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    enum_value = getattr(value, "value", None)
    if enum_value is not None:
        return enum_value
    return str(value)


def _mapping_value(mapping: Mapping[str, Any], keys: Sequence[str]) -> Any:
    for key in keys:
        if key in mapping:
            return mapping[key]
    return None


def _extract_gate_result(result: Any) -> tuple[bool, Any]:
    trusted: Any = None
    risk: Any = None

    if isinstance(result, Mapping):
        candidates = [result]
        for nested_key in ("result", "details", "gate", "decision", "payload"):
            nested = result.get(nested_key)
            if isinstance(nested, Mapping):
                candidates.append(nested)

        for candidate in candidates:
            if trusted is None:
                trusted = _mapping_value(
                    candidate,
                    (
                        "trusted",
                        "is_trusted",
                        "trusted_verdict",
                        "verdict",
                        "decision",
                    ),
                )
            if risk is None:
                risk = _mapping_value(
                    candidate,
                    (
                        "published_overall_risk",
                        "overall_risk",
                        "published_risk",
                        "risk_tier",
                        "risk",
                    ),
                )

    elif hasattr(result, "__dict__") or hasattr(result, "__slots__"):
        for attribute in (
            "trusted",
            "is_trusted",
            "trusted_verdict",
            "verdict",
            "decision",
        ):
            if trusted is None and hasattr(result, attribute):
                trusted = getattr(result, attribute)
                break
        for attribute in (
            "published_overall_risk",
            "overall_risk",
            "published_risk",
            "risk_tier",
            "risk",
        ):
            if risk is None and hasattr(result, attribute):
                risk = getattr(result, attribute)
                break

    elif isinstance(result, (tuple, list)):
        for item in result:
            if isinstance(item, Mapping):
                nested_trusted, nested_risk = _extract_gate_result(item)
                if trusted is None and nested_trusted is not None:
                    trusted = nested_trusted
                if risk is None and nested_risk is not None:
                    risk = nested_risk
            elif isinstance(item, bool):
                if trusted is None:
                    trusted = item
            elif isinstance(item, str):
                normalized = item.strip().lower()
                if trusted is None and (
                    normalized in _TRUSTED_VALUES or normalized in _UNTRUSTED_VALUES
                ):
                    trusted = item
                elif risk is None:
                    risk = item
            elif risk is None:
                risk = item
    elif trusted is None:
        trusted = result

    if trusted is None:
        trusted = False

    return _as_bool(trusted), _risk_value(risk)


def _discover_valid_axis_names() -> set[str] | None:
    sources = [trust_gating_override]
    gate_function = getattr(trust_gating_override, "trust_gate", None)
    if gate_function is not None:
        function_globals = getattr(gate_function, "__globals__", None)
        if function_globals:
            sources.append(function_globals)

        for cell in getattr(gate_function, "__closure__", None) or ():
            try:
                value = cell.cell_contents
            except ValueError:
                continue
            if isinstance(value, (set, frozenset, tuple, list)):
                sources.append({"VALID_AXES": value})
            elif isinstance(value, Mapping):
                sources.append({"VALID_AXES": value})

    explicit_attributes = (
        "VALID_AXES",
        "VALID_AXIS_NAMES",
        "SUPPORTED_AXES",
        "SUPPORTED_AXIS_NAMES",
        "AXIS_NAMES",
    )

    for source in sources:
        for attribute in explicit_attributes:
            value = source.get(attribute) if isinstance(source, Mapping) else getattr(
                source, attribute, None
            )
            if isinstance(value, Mapping):
                names = {str(name) for name in value.keys()}
            elif isinstance(value, (set, frozenset, tuple, list)):
                names = {str(name) for name in value if isinstance(name, str)}
            else:
                continue
            if names:
                return names

    return None


def get_derived_tier(
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    """Return derived trust-gate verdicts and their agreement with the registry."""
    registry_rows = session.execute(
        select(McpServerRegistry).order_by(McpServerRegistry.server_id)
    ).scalars().all()

    axis_rows = session.execute(
        select(McpLlmAxisScore).order_by(
            McpLlmAxisScore.server_id,
            McpLlmAxisScore.axis_name,
        )
    ).scalars().all()

    axes_by_server: dict[Any, dict[str, Any]] = {}
    valid_axis_names = _discover_valid_axis_names()

    for axis_row in axis_rows:
        axis_name = axis_row.axis_name
        if not isinstance(axis_name, str) or not axis_name:
            continue
        if valid_axis_names is not None and axis_name not in valid_axis_names:
            continue

        axis_labels = axes_by_server.setdefault(axis_row.server_id, {})
        axis_labels[axis_name] = axis_row.label
        _ = axis_row.p_top

    servers: list[dict[str, Any]] = []
    trusted_count = 0
    mismatch_count = 0

    for registry in registry_rows:
        axis_labels = axes_by_server.get(registry.server_id, {})
        gate_result = trust_gating_override.trust_gate(
            registry.url,
            registry.name,
            axis_labels,
        )
        derived_trusted, derived_risk = _extract_gate_result(gate_result)
        stored_trusted = _verdict_to_bool(registry.verdict)
        match = stored_trusted == derived_trusted

        if derived_trusted:
            trusted_count += 1
        if not match:
            mismatch_count += 1

        servers.append(
            {
                "server_id": registry.server_id,
                "name": registry.name,
                "stored_verdict": registry.verdict,
                "derived_trusted": derived_trusted,
                "derived_published_overall_risk": derived_risk,
                "match": match,
            }
        )

    total = len(servers)
    return {
        "servers": servers,
        "summary": {
            "total": total,
            "trusted": trusted_count,
            "untrusted": total - trusted_count,
            "mismatches": mismatch_count,
        },
    }


get_derived_tier_data = get_derived_tier
get_derived_tiers = get_derived_tier
get_trust_gate_derived_tier = get_derived_tier
get_trust_gate_derived_tier_data = get_derived_tier
get_derived_tier_api = get_derived_tier


if __name__ == "__main__":
    trusted, risk = _extract_gate_result(
        {"trusted": True, "published_overall_risk": "low"}
    )
    assert trusted is True
    assert risk == "low"
    assert _verdict_to_bool("trusted") is True
    assert _verdict_to_bool("untrusted") is False
    assert _extract_gate_result((True, "high")) == (True, "high")
    print("PASS")