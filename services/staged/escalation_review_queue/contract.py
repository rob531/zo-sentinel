from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Query
from fastapi.encoders import jsonable_encoder
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore, McpScoreDispute, McpServerRegistry, Org, User

router = APIRouter(prefix="/api")


def _column_values(row: Any) -> dict[str, Any]:
    return {
        column.name: getattr(row, column.name, None)
        for column in row.__table__.columns
    }


def _first_value(values: dict[str, Any], names: tuple[str, ...]) -> Any:
    normalized = {key.lower().replace("-", "_"): value for key, value in values.items()}
    for name in names:
        value = normalized.get(name.lower())
        if value is not None:
            return value
    return None


def _as_float(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _text(value: Any) -> str | None:
    if value is None:
        return None
    return str(value)


def _server_values(values: dict[str, Any]) -> tuple[Any, str | None]:
    server_id = _first_value(
        values,
        ("server_id", "mcp_server_id", "server_key", "registry_id", "id", "slug"),
    )
    server_name = _first_value(
        values,
        ("server_name", "name", "display_name", "label", "repository", "package"),
    )
    return server_id, _text(server_name)


def _score_server_id(values: dict[str, Any]) -> str | None:
    value = _first_value(
        values,
        (
            "server_id",
            "mcp_server_id",
            "server_key",
            "registry_id",
            "server_fk",
            "server_fk_id",
            "server_name",
        ),
    )
    return None if value is None else str(value)


def _axis_name(values: dict[str, Any]) -> str:
    value = _first_value(
        values,
        ("axis", "axis_name", "dimension", "category", "axis_key", "name"),
    )
    return _text(value) or "overall"


def _score_value(values: dict[str, Any]) -> float | None:
    value = _first_value(
        values,
        (
            "score",
            "llm_score",
            "axis_score",
            "value",
            "numeric_score",
            "risk_score",
        ),
    )
    return _as_float(value)


def _confidence(values: dict[str, Any]) -> float | None:
    value = _first_value(
        values,
        ("confidence", "confidence_score", "certainty", "model_confidence"),
    )
    return _as_float(value)


def _status(values: dict[str, Any]) -> str | None:
    value = _first_value(values, ("status", "state", "review_status", "resolution"))
    return _text(value)


def _is_open_dispute(status: str | None) -> bool:
    if status is None:
        return True
    return status.lower().replace("-", "_") not in {
        "closed",
        "resolved",
        "accepted",
        "rejected",
        "dismissed",
        "complete",
        "completed",
    }


def _threshold(value: float, threshold: float) -> bool:
    if value <= 1.0:
        return value >= min(threshold / 100.0, 1.0)
    return value >= threshold


def _confidence_is_low(value: float, minimum: float) -> bool:
    if value <= 1.0:
        return value < minimum
    return value < minimum * 100.0


def _serialize_dispute(values: dict[str, Any]) -> dict[str, Any]:
    score = _first_value(
        values,
        (
            "disputed_score",
            "proposed_score",
            "challenged_score",
            "score",
            "value",
            "original_score",
        ),
    )
    return {
        "id": _first_value(values, ("id", "dispute_id", "uuid")),
        "axis": _axis_name(values),
        "score": _as_float(score),
        "status": _status(values) or "open",
        "reason": _text(
            _first_value(values, ("reason", "rationale", "details", "description"))
        ),
        "created_at": _first_value(values, ("created_at", "submitted_at", "created")),
    }


def _build_queue(
    session: Session,
    threshold: float,
    minimum_confidence: float,
    limit: int,
) -> list[dict[str, Any]]:
    registry_rows = list(session.execute(select(McpServerRegistry)).scalars())
    score_rows = list(session.execute(select(McpLlmAxisScore)).scalars())
    dispute_rows = list(session.execute(select(McpScoreDispute)).scalars())

    servers: dict[str, dict[str, Any]] = {}
    for row in registry_rows:
        values = _column_values(row)
        server_id, server_name = _server_values(values)
        key = str(server_id) if server_id is not None else str(id(row))
        servers[key] = {
            "server_id": server_id,
            "server_name": server_name,
            "registry": values,
            "scores": [],
            "disputes": [],
        }

    for row in score_rows:
        values = _column_values(row)
        key = _score_server_id(values)
        if key is None:
            continue
        if key not in servers:
            servers[key] = {
                "server_id": key,
                "server_name": key,
                "registry": {},
                "scores": [],
                "disputes": [],
            }
        servers[key]["scores"].append(values)

    for row in dispute_rows:
        values = _column_values(row)
        key = _score_server_id(values)
        if key is None:
            continue
        if key not in servers:
            servers[key] = {
                "server_id": key,
                "server_name": key,
                "registry": {},
                "scores": [],
                "disputes": [],
            }
        servers[key]["disputes"].append(values)

    queue: list[dict[str, Any]] = []
    for key, server in servers.items():
        axis_scores: dict[str, float] = {}
        confidence_values: list[float] = []
        numeric_scores: list[float] = []
        model_names: list[str] = []

        for values in server["scores"]:
            axis = _axis_name(values)
            score = _score_value(values)
            if score is not None:
                axis_scores[axis] = score
                numeric_scores.append(score)
            confidence = _confidence(values)
            if confidence is not None:
                confidence_values.append(confidence)
            model = _text(
                _first_value(values, ("model", "model_name", "llm_model", "provider"))
            )
            if model and model not in model_names:
                model_names.append(model)

        open_disputes = [
            dispute
            for dispute in server["disputes"]
            if _is_open_dispute(_status(dispute))
        ]
        maximum_score = max(numeric_scores) if numeric_scores else None
        confidence = min(confidence_values) if confidence_values else None

        reasons: list[str] = []
        if maximum_score is not None and _threshold(maximum_score, threshold):
            reasons.append("llm_score_at_or_above_threshold")
        if confidence is not None and _confidence_is_low(confidence, minimum_confidence):
            reasons.append("low_model_confidence")
        if open_disputes:
            reasons.append("open_score_dispute")

        if not reasons:
            continue

        if open_disputes or (maximum_score is not None and _threshold(maximum_score, threshold)):
            priority = "high"
        else:
            priority = "medium"

        registry = server["registry"]
        queue.append(
            {
                "server_id": server["server_id"],
                "server_name": server["server_name"],
                "llm_score": maximum_score,
                "axis_scores": axis_scores,
                "confidence": confidence,
                "models": model_names,
                "disputes": [
                    jsonable_encoder(_serialize_dispute(dispute))
                    for dispute in open_disputes
                ],
                "escalation_reasons": reasons,
                "priority": priority,
                "status": "needs_review",
                "registry_status": _status(registry),
                "updated_at": _first_value(
                    registry,
                    ("updated_at", "last_scored_at", "modified_at", "updated"),
                ),
            }
        )

    queue.sort(
        key=lambda item: (
            0 if item["priority"] == "high" else 1,
            -(item["llm_score"] if item["llm_score"] is not None else -1),
            str(item["server_id"]),
        )
    )
    return queue[:limit]


@router.get("/escalation-review-queue")
@router.get("/escalation_review_queue")
@router.get("/review-queue")
@router.get("/review_queue")
def get_escalation_review_queue(
    threshold: float = Query(70.0, ge=0.0, le=100.0),
    minimum_confidence: float = Query(0.6, ge=0.0, le=1.0),
    limit: int = Query(100, ge=1, le=1000),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    items = _build_queue(session, threshold, minimum_confidence, limit)
    return {
        "items": items,
        "queue": items,
        "count": len(items),
        "total": len(items),
        "threshold": threshold,
        "minimum_confidence": minimum_confidence,
    }


@router.get("/escalation-review-queue/{server_id}")
@router.get("/escalation_review_queue/{server_id}")
def get_escalation_review_item(
    server_id: str,
    threshold: float = Query(70.0, ge=0.0, le=100.0),
    minimum_confidence: float = Query(0.6, ge=0.0, le=1.0),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    for item in _build_queue(session, threshold, minimum_confidence, 1000):
        if str(item["server_id"]) == server_id:
            return item
    raise HTTPException(status_code=404, detail="Escalation review item not found")


def create_app() -> FastAPI:
    application = FastAPI(title="Escalation Review Queue")
    application.include_router(router)
    return application


app = create_app()


def run_self_test() -> bool:
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestSession = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    metadata = McpServerRegistry.__table__.metadata
    metadata.create_all(
        engine,
        tables=[
            Org.__table__,
            User.__table__,
            McpServerRegistry.__table__,
            McpLlmAxisScore.__table__,
            McpScoreDispute.__table__,
        ],
        checkfirst=True,
    )

    database = TestSession()
    that_app = FastAPI()
    that_app.include_router(router)

    def override_get_session():
        return database

    that_app.dependency_overrides[get_session] = override_get_session

    try:
        with TestClient(that_app) as client:
            response = client.get("/api/escalation-review-queue")
            assert response.status_code == 200, response.text
            payload = response.json()
            assert isinstance(payload, dict), payload
            assert isinstance(payload["items"], list), payload
            assert payload["count"] == len(payload["items"]), payload

            response = client.get(
                "/api/escalation-review-queue",
                params={"threshold": 80, "minimum_confidence": 0.5, "limit": 10},
            )
            assert response.status_code == 200, response.text
            assert "items" in response.json()
    finally:
        that_app.dependency_overrides.clear()
        database.close()
        engine.dispose()

    return True


def run_contract() -> bool:
    return run_self_test()


if __name__ == "__main__":
    run_self_test()
    print("PASS")