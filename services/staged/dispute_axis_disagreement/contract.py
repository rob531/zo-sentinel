from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, FastAPI, Query
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import McpLlmAxisScore, McpScoreDispute

router = APIRouter(prefix="/api/disputes", tags=["disputes"])


def compute_axis_disagreement(rows: list[dict]) -> dict:
    if not rows:
        raise ValueError("rows must contain at least one joined axis row")

    row = rows[0]
    proposed_label = row.get("proposed_label")
    model_label = row.get("model_label")

    if proposed_label is not None:
        proposed_label = str(proposed_label)
    if model_label is not None:
        model_label = str(model_label)

    disagree = (
        proposed_label is not None
        and model_label is not None
        and proposed_label.strip().casefold() != model_label.strip().casefold()
    )

    return {
        "server_id": row.get("server_id"),
        "axis_name": row.get("axis_name"),
        "proposed_label": proposed_label,
        "model_label": model_label,
        "disagree": disagree,
    }


def _parse_proposed_axes(value: Any) -> list[tuple[str, str]]:
    parsed = value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise ValueError("proposed_axes is not valid JSON") from exc

    proposals: list[tuple[str, str]] = []

    if isinstance(parsed, dict):
        if "axes" in parsed:
            axes = parsed["axes"]
            if not isinstance(axes, list):
                raise ValueError("proposed_axes.axes must be a list")
            for entry in axes:
                if not isinstance(entry, dict):
                    raise ValueError("invalid proposed axis")
                axis_name = entry.get("axis_name", entry.get("axis"))
                proposed_label = entry.get("proposed_label", entry.get("label"))
                if not isinstance(axis_name, str) or not axis_name.strip():
                    raise ValueError("missing proposed axis name")
                if proposed_label is None or not isinstance(
                    proposed_label, (str, int, float, bool)
                ):
                    raise ValueError("missing proposed axis label")
                proposals.append((axis_name.strip(), str(proposed_label)))
        else:
            for axis_name, proposed_label in parsed.items():
                if not isinstance(axis_name, str) or not axis_name.strip():
                    raise ValueError("invalid proposed axis name")
                if isinstance(proposed_label, dict):
                    proposed_label = proposed_label.get(
                        "proposed_label", proposed_label.get("label")
                    )
                if proposed_label is None or not isinstance(
                    proposed_label, (str, int, float, bool)
                ):
                    raise ValueError("invalid proposed axis label")
                proposals.append((axis_name.strip(), str(proposed_label)))

    elif isinstance(parsed, list):
        for entry in parsed:
            if not isinstance(entry, dict):
                raise ValueError("invalid proposed axis")
            axis_name = entry.get("axis_name", entry.get("axis"))
            proposed_label = entry.get("proposed_label", entry.get("label"))
            if not isinstance(axis_name, str) or not axis_name.strip():
                raise ValueError("missing proposed axis name")
            if proposed_label is None or not isinstance(
                proposed_label, (str, int, float, bool)
            ):
                raise ValueError("missing proposed axis label")
            proposals.append((axis_name.strip(), str(proposed_label)))
    else:
        raise ValueError("proposed_axes must be a JSON object or list")

    if not proposals:
        raise ValueError("proposed_axes contains no axes")

    return proposals


def _score_sort_key(scored_at: datetime | None) -> tuple[bool, str]:
    if scored_at is None:
        return (False, "")
    return (True, scored_at.isoformat())


@router.get("/axis-disagreement")
def get_axis_disagreement(
    status: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=1000),
    session: Session = Depends(get_session),
) -> dict:
    dispute_query = select(
        McpScoreDispute.id,
        McpScoreDispute.server_id,
        McpScoreDispute.proposed_axes,
    ).order_by(McpScoreDispute.created_at, McpScoreDispute.id)

    if status is not None and status.strip():
        dispute_query = dispute_query.where(McpScoreDispute.status == status)

    dispute_rows = session.execute(dispute_query.limit(limit)).all()
    dispute_ids = [row.id for row in dispute_rows]

    joined_rows: list[Any] = []
    if dispute_ids:
        joined_rows = session.execute(
            select(
                McpScoreDispute.id.label("dispute_id"),
                McpScoreDispute.server_id.label("server_id"),
                McpScoreDispute.proposed_axes.label("proposed_axes"),
                McpLlmAxisScore.axis_name.label("model_axis_name"),
                McpLlmAxisScore.label.label("model_label"),
                McpLlmAxisScore.scored_at.label("scored_at"),
            )
            .outerjoin(
                McpLlmAxisScore,
                McpLlmAxisScore.server_id == McpScoreDispute.server_id,
            )
            .where(McpScoreDispute.id.in_(dispute_ids))
            .order_by(
                McpScoreDispute.created_at,
                McpScoreDispute.id,
                McpLlmAxisScore.scored_at,
            )
        ).all()

    scores: dict[tuple[str, str], dict] = {}
    for row in joined_rows:
        if row.model_axis_name is None:
            continue
        key = (str(row.server_id), str(row.model_axis_name))
        candidate = {
            "axis_name": str(row.model_axis_name),
            "model_label": row.model_label,
            "scored_at": row.scored_at,
        }
        current = scores.get(key)
        if current is None or _score_sort_key(row.scored_at) >= _score_sort_key(
            current["scored_at"]
        ):
            scores[key] = candidate

    normalized_scores = {
        (server_id.casefold(), axis_name.casefold()): score
        for (server_id, axis_name), score in scores.items()
    }

    rows_by_dispute: dict[int, list] = {}
    for row in joined_rows:
        rows_by_dispute.setdefault(row.dispute_id, []).append(row)

    items: list[dict] = []
    aggregation: dict[str, dict[str, int]] = {}
    unparsed = 0

    for dispute_row in dispute_rows:
        try:
            proposals = _parse_proposed_axes(dispute_row.proposed_axes)
        except ValueError:
            unparsed += 1
            continue

        for axis_name, proposed_label in proposals:
            score = normalized_scores.get(
                (str(dispute_row.server_id).casefold(), axis_name.casefold())
            )
            item = compute_axis_disagreement(
                [
                    {
                        "server_id": dispute_row.server_id,
                        "axis_name": axis_name,
                        "proposed_label": proposed_label,
                        "model_label": score["model_label"] if score else None,
                    }
                ]
            )
            items.append(item)

            axis = aggregation.setdefault(
                axis_name, {"axis_name": axis_name, "disagreements": 0, "count": 0}
            )
            axis["count"] += 1
            if item["disagree"]:
                axis["disagreements"] += 1

    if unparsed:
        aggregation["unparsed"] = {
            "axis_name": "unparsed",
            "disagreements": 0,
            "count": unparsed,
        }

    by_axis = sorted(aggregation.values(), key=lambda axis: axis["axis_name"])

    return {
        "total": len(dispute_rows),
        "by_axis": by_axis,
        "items": items,
    }


def _self_test() -> None:
    engine = create_engine(
        "sqlite+pysqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    McpLlmAxisScore.__table__.create(bind=engine, checkfirst=True)
    McpScoreDispute.__table__.create(bind=engine, checkfirst=True)

    with session_factory() as session:
        session.add_all(
            [
                McpLlmAxisScore(
                    id=1001,
                    server_id="contract-server-1",
                    axis_name="auth_strength",
                    label="strong",
                    model_version="contract-model-v1",
                ),
                McpLlmAxisScore(
                    id=1002,
                    server_id="contract-server-2",
                    axis_name="auth_strength",
                    label="strong",
                    model_version="contract-model-v1",
                ),
                McpScoreDispute(
                    id=2001,
                    server_id="contract-server-1",
                    status="open",
                    proposed_overall_risk="high",
                    proposed_axes=json.dumps(
                        [
                            {
                                "axis_name": "auth_strength",
                                "proposed_label": "weak",
                            }
                        ]
                    ),
                    explanation="Contract disagreement one",
                    reason_category="model_mismatch",
                    submitted_by="contract-analyst",
                ),
                McpScoreDispute(
                    id=2002,
                    server_id="contract-server-2",
                    status="open",
                    proposed_overall_risk="high",
                    proposed_axes=json.dumps(
                        {
                            "axes": [
                                {
                                    "axis_name": "auth_strength",
                                    "proposed_label": "weak",
                                }
                            ]
                        }
                    ),
                    explanation="Contract disagreement two",
                    reason_category="model_mismatch",
                    submitted_by="contract-analyst",
                ),
                McpScoreDispute(
                    id=2003,
                    server_id="contract-server-3",
                    status="open",
                    proposed_overall_risk="medium",
                    proposed_axes="{not-valid-json",
                    explanation="Malformed proposal contract row",
                    reason_category="model_mismatch",
                    submitted_by="contract-analyst",
                ),
            ]
        )
        session.commit()

    def override_get_session():
        with session_factory() as session:
            yield session

    test_app = FastAPI()
    test_app.include_router(router)
    test_app.dependency_overrides[get_session] = override_get_session

    with TestClient(test_app) as client:
        response = client.get("/api/disputes/axis-disagreement")
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["total"] == 3, payload
        auth_strength = next(
            axis
            for axis in payload["by_axis"]
            if axis["axis_name"] == "auth_strength"
        )
        assert auth_strength["disagreements"] == 2, payload
        unparsed = next(
            axis for axis in payload["by_axis"] if axis["axis_name"] == "unparsed"
        )
        assert unparsed["count"] == 1, payload
        assert len(payload["items"]) == 2, payload
        assert all(item["disagree"] for item in payload["items"]), payload

    engine.dispose()
    print("PASS")


run_self_test = _self_test
main = _self_test


if __name__ == "__main__":
    _self_test()