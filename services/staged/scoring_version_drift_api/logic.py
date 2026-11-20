"""logic.py -- scoring version drift computation over the real application data."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Mapping

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry


def _as_timestamp(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        text = value.strip()
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        try:
            return datetime.fromisoformat(text)
        except ValueError:
            return None
    return None


def _timestamp_text(value: Any) -> str | None:
    timestamp = _as_timestamp(value)
    if timestamp is None:
        return None
    if timestamp.tzinfo is not None:
        return timestamp.astimezone(timezone.utc).isoformat()
    return timestamp.isoformat()


def _version_key(value: Any) -> str:
    return "unknown" if value is None else str(value)


def compute_version_drift(rows: list[dict], window_days: int = 90) -> dict:
    """Compute version distributions from score rows.

    ``rows`` contains the selected mcp_llm_axis_scores fields and may also
    contain registry fields.  No database access is performed by this pure
    computation function.
    """
    normalized = [dict(row) for row in rows]
    total_rows = len(normalized)

    model_counts: dict[str, int] = {}
    model_servers: dict[str, set[str]] = {}
    rule_counts: dict[str, int] = {}
    rule_servers: dict[str, set[str]] = {}
    adapter_counts: dict[str, int] = {}
    server_models: dict[str, set[str]] = {}
    timestamps: list[datetime] = []

    for row in normalized:
        model = _version_key(row.get("model_version"))
        rule = _version_key(row.get("decision_rule_version"))
        adapter = _version_key(row.get("adapter_sha256"))
        server = _version_key(row.get("server_id"))

        model_counts[model] = model_counts.get(model, 0) + 1
        model_servers.setdefault(model, set()).add(server)
        rule_counts[rule] = rule_counts.get(rule, 0) + 1
        rule_servers.setdefault(rule, set()).add(server)
        adapter_counts[adapter] = adapter_counts.get(adapter, 0) + 1
        server_models.setdefault(server, set()).add(model)

        timestamp = _as_timestamp(row.get("scored_at"))
        if timestamp is not None:
            timestamps.append(timestamp)

    models = [
        {
            "model_version": version,
            "rows": count,
            "servers": len(model_servers[version]),
            "share_pct": round((count * 100.0 / total_rows), 2)
            if total_rows
            else 0.0,
        }
        for version, count in sorted(model_counts.items())
    ]
    rule_versions = [
        {
            "decision_rule_version": version,
            "rows": count,
            "servers": len(rule_servers[version]),
        }
        for version, count in sorted(rule_counts.items())
    ]
    adapters = [
        {"adapter_sha256": version, "rows": count}
        for version, count in sorted(adapter_counts.items())
    ]

    multiple_version_servers = sorted(
        server for server, versions in server_models.items() if len(versions) > 1
    )

    newest = max(timestamps) if timestamps else None
    oldest = min(timestamps) if timestamps else None
    current_models: set[str] = set()
    if newest is not None:
        for row in normalized:
            timestamp = _as_timestamp(row.get("scored_at"))
            if timestamp == newest:
                current_models.add(_version_key(row.get("model_version")))

    stale_rows = (
        sum(
            count
            for version, count in model_counts.items()
            if version not in current_models
        )
        if current_models
        else 0
    )

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "window_days": int(window_days),
        "models": models,
        "rule_versions": rule_versions,
        "adapters": adapters,
        "servers_scored_by_multiple_versions": multiple_version_servers,
        "newest_score_at": _timestamp_text(newest),
        "oldest_score_at": _timestamp_text(oldest),
        "stale_model_share_pct": round(
            (stale_rows * 100.0 / total_rows), 2
        )
        if total_rows
        else 0.0,
    }


def load_version_drift(db: Session, days: int = 90) -> dict:
    """Read the real score and registry tables and compute version drift."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=int(days))
    statement = (
        select(
            McpLlmAxisScore.model_version,
            McpLlmAxisScore.decision_rule_version,
            McpLlmAxisScore.adapter_sha256,
            McpLlmAxisScore.scored_at,
            McpLlmAxisScore.server_id,
            McpLlmAxisScore.axis_name,
            McpLlmAxisScore.p_top,
            McpServerRegistry.risk_tier.label("registry_risk_tier"),
            McpServerRegistry.name.label("registry_name"),
        )
        .select_from(McpLlmAxisScore)
        .outerjoin(
            McpServerRegistry,
            McpServerRegistry.server_id == McpLlmAxisScore.server_id,
        )
        .where(McpLlmAxisScore.scored_at >= cutoff)
    )
    rows = [dict(row) for row in db.execute(statement).mappings().all()]
    return compute_version_drift(rows, window_days=days)


if __name__ == "__main__":
    test_rows = [
        {
            "model_version": "model-a",
            "decision_rule_version": "rules-1",
            "adapter_sha256": "adapter-a",
            "scored_at": "2024-01-01T00:00:00+00:00",
            "server_id": "server-1",
        },
        {
            "model_version": "model-b",
            "decision_rule_version": "rules-2",
            "adapter_sha256": "adapter-b",
            "scored_at": "2024-01-02T00:00:00+00:00",
            "server_id": "server-1",
        },
        {
            "model_version": "model-a",
            "decision_rule_version": "rules-2",
            "adapter_sha256": "adapter-a",
            "scored_at": "2024-01-03T00:00:00+00:00",
            "server_id": "server-2",
        },
        {
            "model_version": "model-b",
            "decision_rule_version": "rules-1",
            "adapter_sha256": "adapter-b",
            "scored_at": "2024-01-04T00:00:00+00:00",
            "server_id": "server-3",
        },
        {
            "model_version": "model-a",
            "decision_rule_version": "rules-1",
            "adapter_sha256": "adapter-a",
            "scored_at": "2024-01-05T00:00:00+00:00",
            "server_id": "server-4",
        },
    ]
    result = compute_version_drift(test_rows)
    assert len(result["models"]) == 2
    assert "server-1" in result["servers_scored_by_multiple_versions"]
    assert sum(item["rows"] for item in result["models"]) == len(test_rows)
    assert result["newest_score_at"] is not None
    assert result["oldest_score_at"] is not None
    print("PASS")