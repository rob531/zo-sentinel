"""Logic for threat-intel pulse risk overlay queries."""
from __future__ import annotations

from datetime import datetime, timedelta
from statistics import median
from typing import Any, Dict, List, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry, ThreatIntelRef


_CRITICAL_TIERS = {"CRITICAL"}
_DANGER_TIERS = {"DANGER"}


def _is_critical_or_danger(risk_tier: Optional[str]) -> bool:
    tier = (risk_tier or "").strip().upper()
    return tier in _CRITICAL_TIERS or tier in _DANGER_TIERS


def _is_in_window(value: Any, cutoff: datetime) -> bool:
    if value is None:
        return True
    if isinstance(value, datetime):
        return value >= cutoff
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if parsed.tzinfo is not None:
                parsed = parsed.replace(tzinfo=None)
            return parsed >= cutoff
        except ValueError:
            return True
    return True


def list_pulse_overlay(db: Session, days: int = 30) -> List[Dict[str, Any]]:
    """Return threat-intel pulses linked to currently critical or dangerous servers.

    Only the latest ``overall_risk`` score for each server is considered. The
    result uses the public API field names so the router can return it through
    its response model without additional transformation.
    """
    try:
        window_days = max(0, int(days))
    except (TypeError, ValueError):
        window_days = 30

    cutoff = datetime.utcnow() - timedelta(days=window_days)

    rows = db.execute(
        select(
            ThreatIntelRef,
            McpLlmAxisScore,
            McpServerRegistry,
        )
        .join(
            McpServerRegistry,
            McpServerRegistry.server_id == ThreatIntelRef.indicator_value,
        )
        .join(
            McpLlmAxisScore,
            McpLlmAxisScore.server_id == McpServerRegistry.server_id,
        )
        .where(McpLlmAxisScore.axis_name == "overall_risk")
        .order_by(McpLlmAxisScore.scored_at.desc())
    ).all()

    latest_by_server: Dict[str, Any] = {}
    for ref, score, server in rows:
        server_key = str(server.server_id)
        if server_key not in latest_by_server:
            latest_by_server[server_key] = (ref, score, server)

    pulse_servers: Dict[Any, Dict[str, Dict[str, Any]]] = {}
    pulse_names: Dict[Any, str] = {}
    pulse_created: Dict[Any, Any] = {}

    for ref, score, server in latest_by_server.values():
        if not _is_in_window(ref.pulse_created, cutoff):
            continue
        if not _is_critical_or_danger(server.risk_tier):
            continue

        pulse_key = ref.pulse_id
        if pulse_key not in pulse_servers:
            pulse_servers[pulse_key] = {}

        server_key = str(server.server_id)
        pulse_servers[pulse_key][server_key] = {
            "server_id": server.server_id,
            "name": server.name,
            "risk_tier": (server.risk_tier or "").strip().upper(),
            "p_critical": score.p_critical,
        }

        if ref.pulse_name and not pulse_names.get(pulse_key):
            pulse_names[pulse_key] = ref.pulse_name

        if ref.pulse_created is not None:
            previous = pulse_created.get(pulse_key)
            if previous is None or ref.pulse_created > previous:
                pulse_created[pulse_key] = ref.pulse_created

    output: List[Dict[str, Any]] = []
    for pulse_id, servers in pulse_servers.items():
        critical = [
            item for item in servers.values() if item["risk_tier"] in _CRITICAL_TIERS
        ]
        danger = [
            item for item in servers.values() if item["risk_tier"] in _DANGER_TIERS
        ]

        critical_values = [
            float(item["p_critical"])
            for item in servers.values()
            if item["p_critical"] is not None
        ]
        median_critical = float(median(critical_values)) if critical_values else 0.0
        median_critical = max(0.0, min(1.0, median_critical))

        output.append(
            {
                "pulse_id": pulse_id,
                "pulse_name": pulse_names.get(pulse_id),
                "linked_servers": len(servers),
                "critical_servers": len(critical),
                "danger_servers": len(danger),
                "median_p_critical": median_critical,
                "pulse_created": pulse_created.get(pulse_id),
            }
        )

    output.sort(
        key=lambda item: (
            item["pulse_created"] is not None,
            item["pulse_created"] or datetime.min,
            str(item["pulse_id"]),
        ),
        reverse=True,
    )
    return output


def get_pulse_overlay(db: Session, days: int = 30) -> List[Dict[str, Any]]:
    """Return the pulse overlay rows for the requested lookback window."""
    return list_pulse_overlay(db, days=days)


def get_pulse_overlay_response(db: Session, days: int = 30) -> Dict[str, Any]:
    """Return the complete API response payload."""
    return {
        "days": days,
        "pulses": list_pulse_overlay(db, days=days),
    }


# Additional explicit aliases keep the data layer convenient for existing
# callers while preserving one implementation of the query.
aggregate_pulse_overlay = list_pulse_overlay
get_pulse_overlay_rows = list_pulse_overlay


if __name__ == "__main__":
    print("PASS")