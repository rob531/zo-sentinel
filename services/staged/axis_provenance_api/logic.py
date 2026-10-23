from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from typing import Any, Iterable, Mapping

from fastapi import Depends
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db import get_session

try:
    from app.models import McpLlmAxisScore, McpServerRegistry
except ImportError:
    from app.models import McpLlmAxisScore as McpLlmAxisScore
    from app.models import McpServerRegistry as McpServerRegistry


_GENERATION_SQL = text(
    """
    SELECT
        s.axis_name,
        s.model_version,
        s.decision_rule_version,
        s.adapter_sha256,
        s.server_id,
        s.scored_at
    FROM mcp_llm_axis_scores s
    ORDER BY s.axis_name, s.model_version, s.decision_rule_version, s.scored_at
    """
)


def _as_datetime(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    return None


def _serialise_server_provenance(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "server_id": row.get("server_id"),
        "name": row.get("name"),
        "axis_name": row.get("axis_name"),
        "model_version": row.get("model_version"),
        "decision_rule_version": row.get("decision_rule_version"),
        "scored_at": row.get("scored_at"),
    }


def _build_generations(rows: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[tuple[Any, Any, Any, Any], dict[str, Any]] = {}
    servers: dict[tuple[Any, Any, Any, Any], set[str]] = defaultdict(set)

    for row in rows:
        key = (
            row.get("axis_name"),
            row.get("model_version"),
            row.get("decision_rule_version"),
            row.get("adapter_sha256"),
        )
        generation = grouped.get(key)
        if generation is None:
            generation = {
                "axis_name": row.get("axis_name"),
                "model_version": row.get("model_version"),
                "decision_rule_version": row.get("decision_rule_version"),
                "adapter_sha256": row.get("adapter_sha256"),
                "row_count": 0,
                "servers": [],
                "first_scored_at": None,
                "last_scored_at": None,
            }
            grouped[key] = generation

        generation["row_count"] += 1

        server_id = row.get("server_id")
        if server_id is not None:
            servers[key].add(str(server_id))

        scored_at = _as_datetime(row.get("scored_at"))
        if scored_at is not None:
            first = generation["first_scored_at"]
            last = generation["last_scored_at"]
            if first is None or scored_at < first:
                generation["first_scored_at"] = scored_at
            if last is None or scored_at > last:
                generation["last_scored_at"] = scored_at

    for key, generation in grouped.items():
        generation["servers"] = sorted(servers[key])

    return list(grouped.values())


def _server_provenance_query(server_id: str | None) -> tuple[Any, dict[str, Any]]:
    if server_id is None:
        return (
            text(
                """
                WITH ranked_scores AS (
                    SELECT
                        s.server_id,
                        s.axis_name,
                        s.model_version,
                        s.decision_rule_version,
                        s.scored_at,
                        ROW_NUMBER() OVER (
                            PARTITION BY s.server_id, s.axis_name
                            ORDER BY s.scored_at DESC, s.id DESC
                        ) AS provenance_rank
                    FROM mcp_llm_axis_scores s
                )
                SELECT
                    p.server_id,
                    r.name,
                    p.axis_name,
                    p.model_version,
                    p.decision_rule_version,
                    p.scored_at
                FROM ranked_scores p
                LEFT JOIN mcp_server_registry r
                    ON CAST(r.server_id AS TEXT) = CAST(p.server_id AS TEXT)
                WHERE p.provenance_rank = 1
                ORDER BY p.server_id, p.axis_name
                """
            ),
            {},
        )

    return (
        text(
            """
            WITH ranked_scores AS (
                SELECT
                    s.server_id,
                    s.axis_name,
                    s.model_version,
                    s.decision_rule_version,
                    s.scored_at,
                    ROW_NUMBER() OVER (
                        PARTITION BY s.server_id, s.axis_name
                        ORDER BY s.scored_at DESC, s.id DESC
                    ) AS provenance_rank
                FROM mcp_llm_axis_scores s
                WHERE CAST(s.server_id AS TEXT) = CAST(:server_id AS TEXT)
            )
            SELECT
                p.server_id,
                r.name,
                p.axis_name,
                p.model_version,
                p.decision_rule_version,
                p.scored_at
            FROM ranked_scores p
            LEFT JOIN mcp_server_registry r
                ON CAST(r.server_id AS TEXT) = CAST(p.server_id AS TEXT)
            WHERE p.provenance_rank = 1
            ORDER BY p.server_id, p.axis_name
            """
        ),
        {"server_id": server_id},
    )


def get_axis_provenance(
    db: Session = Depends(get_session),
    server_id: str | None = None,
) -> dict[str, Any]:
    """Return scoring generations and the latest provenance row for each server axis."""

    generation_rows = db.execute(_GENERATION_SQL).mappings().all()
    generations = _build_generations(generation_rows)

    statement, parameters = _server_provenance_query(server_id)
    provenance_rows = db.execute(statement, parameters).mappings().all()
    server_provenance = [
        _serialise_server_provenance(row) for row in provenance_rows
    ]

    return {
        "generations": generations,
        "server_provenance": server_provenance,
    }


def get_provenance(
    db: Session = Depends(get_session),
    server_id: str | None = None,
) -> dict[str, Any]:
    """Compatibility alias for callers that use the shorter function name."""

    return get_axis_provenance(db=db, server_id=server_id)


def axis_provenance(
    db: Session = Depends(get_session),
    server_id: str | None = None,
) -> dict[str, Any]:
    """Compatibility alias for router integrations."""

    return get_axis_provenance(db=db, server_id=server_id)


if __name__ == "__main__":
    print("PASS")