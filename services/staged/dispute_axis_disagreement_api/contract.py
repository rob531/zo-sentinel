from __future__ import annotations

import json
import math
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional, Tuple

from fastapi import APIRouter, Depends, FastAPI, Query
from fastapi.testclient import TestClient
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import get_session

try:
    from app.models import McpScoreDispute
except ImportError:
    try:
        from app.models import MCPDispute as McpScoreDispute
    except ImportError:
        from app.models import ScoreDispute as McpScoreDispute


class AxisDisagreement(BaseModel):
    axis: str
    disputes: int
    servers: int
    avg_proposed_value: float


class StatusCount(BaseModel):
    status: str
    count: int


class ReasonCount(BaseModel):
    reason_category: str
    count: int


class ServerCount(BaseModel):
    server_id: str
    count: int


class AxisDisagreementResponse(BaseModel):
    total: int
    by_axis: List[AxisDisagreement]
    by_status: List[StatusCount]
    by_reason: List[ReasonCount]
    top_servers: List[ServerCount]


def _decode_json(value: Any) -> Any:
    if value is None:
        return {}
    if isinstance(value, (dict, list)):
        return value
    if isinstance(value, bytes):
        value = value.decode("utf-8")
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return {}


def _number(value: Any) -> Optional[float]:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        result = float(value)
        return result if math.isfinite(result) else None
    if isinstance(value, str):
        try:
            result = float(value)
        except ValueError:
            return None
        return result if math.isfinite(result) else None
    return None


def _axis_entries(value: Any) -> Iterable[Tuple[str, Optional[float]]]:
    decoded = _decode_json(value)

    if isinstance(decoded, dict):
        for axis, proposed in decoded.items():
            if not isinstance(axis, str):
                continue
            number = _number(proposed)
            if number is None and isinstance(proposed, dict):
                for key in ("proposed_value", "value", "score", "rating"):
                    if key in proposed:
                        number = _number(proposed[key])
                        if number is not None:
                            break
            yield axis, number
        return

    if isinstance(decoded, list):
        for item in decoded:
            if not isinstance(item, dict):
                continue

            axis = item.get("axis") or item.get("name") or item.get("axis_name")
            if not isinstance(axis, str):
                continue

            proposed = item.get("proposed_value")
            if proposed is None:
                proposed = item.get("value")
            if proposed is None:
                proposed = item.get("score")
            if proposed is None:
                proposed = item.get("rating")

            number = _number(proposed)
            yield axis, number


def _label(value: Any) -> str:
    if value is None:
        return "unknown"
    return str(value)


def _cutoff(days: int) -> datetime:
    return datetime.now(timezone.utc) - timedelta(days=days)


def get_axis_disagreement_report(
    session: Session,
    days: int = 90,
) -> AxisDisagreementResponse:
    cutoff = _cutoff(days)
    rows = session.execute(
        text(
            """
            SELECT
                id,
                server_id,
                proposed_axes,
                status,
                reason_category
            FROM mcp_score_disputes
            WHERE created_at >= :cutoff
            ORDER BY id
            """
        ),
        {"cutoff": cutoff},
    ).mappings().all()

    axis_disputes: Dict[str, int] = {}
    axis_servers: Dict[str, set[str]] = {}
    axis_values: Dict[str, List[float]] = {}
    status_counts: Dict[str, int] = {}
    reason_counts: Dict[str, int] = {}
    server_counts: Dict[str, int] = {}

    for row in rows:
        server_id = _label(row["server_id"])
        status = _label(row["status"])
        reason = _label(row["reason_category"])

        status_counts[status] = status_counts.get(status, 0) + 1
        reason_counts[reason] = reason_counts.get(reason, 0) + 1
        server_counts[server_id] = server_counts.get(server_id, 0) + 1

        for axis, proposed_value in _axis_entries(row["proposed_axes"]):
            axis_disputes[axis] = axis_disputes.get(axis, 0) + 1
            axis_servers.setdefault(axis, set()).add(server_id)
            if proposed_value is not None:
                axis_values.setdefault(axis, []).append(proposed_value)

    by_axis = [
        AxisDisagreement(
            axis=axis,
            disputes=axis_disputes[axis],
            servers=len(axis_servers[axis]),
            avg_proposed_value=(
                sum(axis_values[axis]) / len(axis_values[axis])
                if axis_values.get(axis)
                else 0.0
            ),
        )
        for axis in sorted(axis_disputes)
    ]

    by_status = [
        StatusCount(status=status, count=count)
        for status, count in sorted(status_counts.items())
    ]
    by_reason = [
        ReasonCount(reason_category=reason, count=count)
        for reason, count in sorted(reason_counts.items())
    ]
    top_servers = [
        ServerCount(server_id=server_id, count=count)
        for server_id, count in sorted(
            server_counts.items(), key=lambda item: (-item[1], item[0])
        )[:10]
    ]

    return AxisDisagreementResponse(
        total=len(rows),
        by_axis=by_axis,
        by_status=by_status,
        by_reason=by_reason,
        top_servers=top_servers,
    )


router = APIRouter(prefix="/api")


@router.get(
    "/disputes/axis-disagreement",
    response_model=AxisDisagreementResponse,
)
def axis_disagreement_endpoint(
    days: int = Query(default=90, ge=1),
    session: Session = Depends(get_session),
) -> AxisDisagreementResponse:
    return get_axis_disagreement_report(session=session, days=days)


app = FastAPI()
app.include_router(router)


def _create_test_engine() -> Engine:
    from sqlalchemy import create_engine

    return create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )


def _seed_test_data(session: Session) -> None:
    session.execute(
        text(
            """
            CREATE TABLE mcp_score_disputes (
                id INTEGER PRIMARY KEY,
                server_id VARCHAR(255) NOT NULL,
                submitted_by VARCHAR(255),
                proposed_overall_risk FLOAT,
                proposed_axes TEXT,
                reason_category VARCHAR(255),
                explanation TEXT,
                status VARCHAR(64),
                admin_note TEXT,
                created_at DATETIME NOT NULL,
                resolved_at DATETIME
            )
            """
        )
    )

    now = datetime.now(timezone.utc)
    session.execute(
        text(
            """
            INSERT INTO mcp_score_disputes
                (id, server_id, submitted_by, proposed_overall_risk,
                 proposed_axes, reason_category, explanation, status,
                 admin_note, created_at, resolved_at)
            VALUES
                (:id, :server_id, :submitted_by, :proposed_overall_risk,
                 :proposed_axes, :reason_category, :explanation, :status,
                 :admin_note, :created_at, :resolved_at)
            """
        ),
        [
            {
                "id": 1,
                "server_id": "server-alpha",
                "submitted_by": "reviewer-1",
                "proposed_overall_risk": 0.8,
                "proposed_axes": json.dumps({"auth_strength": 0.8}),
                "reason_category": "scoring",
                "explanation": "First auth-strength dispute",
                "status": "open",
                "admin_note": None,
                "created_at": now,
                "resolved_at": None,
            },
            {
                "id": 2,
                "server_id": "server-alpha",
                "submitted_by": "reviewer-2",
                "proposed_overall_risk": 0.6,
                "proposed_axes": json.dumps({"auth_strength": 0.6}),
                "reason_category": "scoring",
                "explanation": "Second auth-strength dispute",
                "status": "open",
                "admin_note": None,
                "created_at": now,
                "resolved_at": None,
            },
            {
                "id": 3,
                "server_id": "server-beta",
                "submitted_by": "reviewer-3",
                "proposed_overall_risk": 0.4,
                "proposed_axes": json.dumps({"network_egress": 0.4}),
                "reason_category": "evidence",
                "explanation": "Network-egress dispute",
                "status": "resolved",
                "admin_note": "Resolved",
                "created_at": now,
                "resolved_at": now,
            },
        ],
    )
    session.commit()


def main() -> None:
    engine = _create_test_engine()
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    test_session = SessionLocal()

    _seed_test_data(test_session)

    def override_get_session():
        yield test_session

    app.dependency_overrides[get_session] = override_get_session

    try:
        with TestClient(app) as client:
            response = client.get("/api/disputes/axis-disagreement")
            assert response.status_code == 200, response.text
            payload = response.json()

            assert payload["total"] == 3, payload
            auth_strength = next(
                item for item in payload["by_axis"] if item["axis"] == "auth_strength"
            )
            assert auth_strength["disputes"] == 2, payload
            assert auth_strength["servers"] == 1, payload
            assert any(
                item["status"] == "resolved" and item["count"] == 1
                for item in payload["by_status"]
            ), payload

        print("PASS")
    finally:
        app.dependency_overrides.pop(get_session, None)
        test_session.close()
        engine.dispose()


if __name__ == "__main__":
    main()