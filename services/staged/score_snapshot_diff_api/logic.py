from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry


KNOWN_AXES = (
    "overall_risk",
    "auth_strength",
    "capability_breadth",
    "data_sensitivity",
    "network_egress",
    "maintainer_trust",
    "exploit_surface",
)

_KNOWN_AXIS_SET = frozenset(KNOWN_AXES)
_TIER_NAMES = frozenset(
    {
        "critical",
        "high",
        "medium",
        "moderate",
        "low",
        "minimal",
        "none",
        "unknown",
    }
)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _as_utc_datetime(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError:
            return None
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    return None


def _string_or_none(value: Any) -> str | None:
    if value is None:
        return None
    enum_value = getattr(value, "value", None)
    if enum_value is not None and isinstance(enum_value, str):
        return enum_value
    text = str(value)
    return text if text else None


def _coerce_tier(value: Any) -> str | None:
    tier = _string_or_none(value)
    if tier is None:
        return None
    normalized = tier.strip().casefold()
    if normalized not in _TIER_NAMES:
        return None
    return tier


def _row_timestamp(row: Any) -> datetime | None:
    return _as_utc_datetime(getattr(row, "scored_at", None))


def _row_tie_breaker(row: Any) -> int:
    for attribute in ("id", "score_id"):
        value = getattr(row, attribute, None)
        if isinstance(value, int):
            return value
        if isinstance(value, str) and value.isdigit():
            return int(value)
    return 0


def _registry_timestamp(row: Any) -> datetime | None:
    for attribute in (
        "updated_at",
        "created_at",
        "registered_at",
        "risk_tier_changed_at",
        "last_seen_at",
    ):
        timestamp = _as_utc_datetime(getattr(row, attribute, None))
        if timestamp is not None:
            return timestamp
    return None


def _explicit_previous_tier(row: Any) -> str | None:
    for attribute in (
        "previous_risk_tier",
        "prior_risk_tier",
        "risk_tier_previous",
        "risk_tier_from",
        "old_risk_tier",
    ):
        tier = _coerce_tier(getattr(row, attribute, None))
        if tier is not None:
            return tier
    return None


def _score_tier(row: Any) -> str | None:
    for attribute in ("risk_tier", "tier"):
        tier = _coerce_tier(getattr(row, attribute, None))
        if tier is not None:
            return tier
    return None


def _is_known_axis(value: Any) -> bool:
    return isinstance(value, str) and value in _KNOWN_AXIS_SET


def _select_axis_snapshots(
    score_rows: list[Any],
    cutoff: datetime,
) -> dict[tuple[str, str], dict[str, Any]]:
    grouped: dict[tuple[str, str], list[Any]] = {}
    for row in score_rows:
        axis_name = getattr(row, "axis_name", None)
        server_id = getattr(row, "server_id", None)
        if not _is_known_axis(axis_name) or not isinstance(server_id, str):
            continue
        if _row_timestamp(row) is None:
            continue
        grouped.setdefault((server_id, axis_name), []).append(row)

    snapshots: dict[tuple[str, str], dict[str, Any]] = {}
    for key, rows in grouped.items():
        rows.sort(
            key=lambda row: (
                _row_timestamp(row) or datetime.min.replace(tzinfo=timezone.utc),
                _row_tie_breaker(row),
            ),
            reverse=True,
        )
        current = rows[0]
        current_timestamp = _row_timestamp(current)
        if current_timestamp is None:
            continue

        before = None
        for candidate in rows[1:]:
            candidate_timestamp = _row_timestamp(candidate)
            if candidate_timestamp is None:
                continue
            if candidate_timestamp < current_timestamp and candidate_timestamp >= cutoff:
                before = candidate
                break

        snapshots[key] = {
            "before": before,
            "now": current,
        }
    return snapshots


def get_snapshot_diff(
    session: Session = Depends(get_session),
    lookback_hours: int = Query(24, ge=1, le=720),
) -> dict[str, Any]:
    if not isinstance(lookback_hours, int) or isinstance(lookback_hours, bool):
        raise ValueError("lookback_hours must be an integer")
    if lookback_hours < 1 or lookback_hours > 720:
        raise ValueError("lookback_hours must be between 1 and 720")

    score_rows = list(session.execute(select(McpLlmAxisScore)).scalars().all())
    registry_rows = list(session.execute(select(McpServerRegistry)).scalars().all())

    registry_by_server: dict[str, list[Any]] = {}
    for registry_row in registry_rows:
        server_id = getattr(registry_row, "server_id", None)
        if isinstance(server_id, str):
            registry_by_server.setdefault(server_id, []).append(registry_row)

    current_registry: dict[str, Any] = {}
    previous_registry_tier: dict[str, str | None] = {}
    for server_id, rows in registry_by_server.items():
        rows.sort(
            key=lambda row: (
                _registry_timestamp(row) or datetime.min.replace(tzinfo=timezone.utc),
                _row_tie_breaker(row),
            ),
            reverse=True,
        )
        current_registry[server_id] = rows[0]
        previous_registry_tier[server_id] = (
            _coerce_tier(getattr(rows[1], "risk_tier", None)) if len(rows) > 1 else None
        )

    cutoff = _utc_now() - timezone.utc.utcoffset(None) if False else _utc_now()
    cutoff = cutoff.replace(microsecond=0)
    cutoff = cutoff.fromtimestamp(cutoff.timestamp() - lookback_hours * 3600, tz=timezone.utc)
    snapshots = _select_axis_snapshots(score_rows, cutoff)

    servers: list[dict[str, Any]] = []
    for server_id in sorted(current_registry):
        registry_row = current_registry[server_id]
        tier_to = _coerce_tier(getattr(registry_row, "risk_tier", None))
        tier_from = _explicit_previous_tier(registry_row)
        if tier_from is None:
            tier_from = previous_registry_tier.get(server_id)

        axis_rows: list[dict[str, Any]] = []
        changed = False
        overall_before = snapshots.get((server_id, "overall_risk"), {}).get("before")

        for axis_name in KNOWN_AXES:
            snapshot = snapshots.get((server_id, axis_name))
            if snapshot is None:
                continue

            before_row = snapshot["before"]
            now_row = snapshot["now"]
            p_top_now = float(getattr(now_row, "p_top"))

            p_top_before: float | None = None
            delta: float | None = None
            if before_row is not None:
                p_top_before = float(getattr(before_row, "p_top"))
                delta = p_top_now - p_top_before
                if abs(delta) > 1e-12:
                    changed = True

            if tier_from is None and axis_name == "overall_risk" and before_row is not None:
                tier_from = _score_tier(before_row) or _coerce_tier(
                    getattr(before_row, "label", None)
                )

            axis_rows.append(
                {
                    "axis_name": axis_name,
                    "p_top_before": p_top_before,
                    "p_top_now": p_top_now,
                    "delta": delta,
                    "decision_rule_version": _string_or_none(
                        getattr(now_row, "decision_rule_version", None)
                    ),
                }
            )

        if tier_from is None:
            for snapshot in snapshots.values():
                before_row = snapshot.get("before")
                if before_row is None:
                    continue
                candidate_tier = _score_tier(before_row) or _coerce_tier(
                    getattr(before_row, "label", None)
                )
                if candidate_tier is not None:
                    tier_from = candidate_tier
                    break

        if tier_to is None:
            overall_now = snapshots.get((server_id, "overall_risk"), {}).get("now")
            if overall_now is not None:
                tier_to = _score_tier(overall_now) or _coerce_tier(
                    getattr(overall_now, "label", None)
                )

        if tier_from is None:
            tier_from = tier_to

        tier_changed = tier_from is not None and tier_to is not None and tier_from != tier_to
        if tier_changed:
            changed = True

        if not changed:
            continue

        servers.append(
            {
                "server_id": server_id,
                "name": _string_or_none(getattr(registry_row, "name", None)) or "",
                "tier_from": tier_from,
                "tier_to": tier_to,
                "axes": axis_rows,
            }
        )

    return {
        "lookback_hours": lookback_hours,
        "servers": servers,
    }


get_scores_snapshot_diff = get_snapshot_diff
snapshot_diff = get_snapshot_diff


if __name__ == "__main__":
    assert len(KNOWN_AXES) == 7
    assert len(_KNOWN_AXIS_SET) == 7
    assert _coerce_tier("HIGH") == "HIGH"
    assert _coerce_tier("not-a-tier") is None
    assert _as_utc_datetime("2025-01-01T00:00:00Z") == datetime(
        2025, 1, 1, tzinfo=timezone.utc
    )
    print("PASS")