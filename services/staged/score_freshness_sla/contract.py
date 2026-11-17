from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Iterator

from fastapi import APIRouter, Depends, FastAPI, Query
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry


AXIS_NAMES = (
    "overall_risk",
    "auth_strength",
    "capability_breadth",
    "data_sensitivity",
    "network_egress",
    "maintainer_trust",
    "exploit_surface",
)

router = APIRouter(prefix="/api")


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _isoformat(value: datetime) -> str:
    return _as_utc(value).isoformat().replace("+00:00", "Z")


@router.get("/scores/freshness")
def get_score_freshness_sla(
    max_age_hours: int = Query(168, ge=0),
    limit: int = Query(500, ge=1, le=5000),
    session: Session = Depends(get_session),
) -> dict:
    now = datetime.now(timezone.utc)

    registry_rows = session.execute(
        select(
            McpServerRegistry.server_id,
            McpServerRegistry.name,
            McpServerRegistry.risk_tier,
        ).order_by(McpServerRegistry.server_id)
    ).all()

    summary_rows = session.execute(
        select(
            McpLlmAxisScore.server_id,
            func.max(McpLlmAxisScore.scored_at),
            func.count(func.distinct(McpLlmAxisScore.axis_name)),
        )
        .where(McpLlmAxisScore.axis_name.in_(AXIS_NAMES))
        .group_by(McpLlmAxisScore.server_id)
    ).all()

    axis_rows = session.execute(
        select(McpLlmAxisScore.server_id, McpLlmAxisScore.axis_name)
        .where(McpLlmAxisScore.axis_name.in_(AXIS_NAMES))
        .distinct()
    ).all()

    summaries = {
        row.server_id: {
            "last_scored_at": row[1],
            "axes_scored": int(row[2] or 0),
        }
        for row in summary_rows
    }
    scored_axes: dict[str, set[str]] = {}
    for server_id, axis_name in axis_rows:
        scored_axes.setdefault(server_id, set()).add(axis_name)

    items = []
    stale_count = 0
    incomplete_axis_count = 0

    for server_id, name, risk_tier in registry_rows:
        summary = summaries.get(server_id)
        last_scored_at = summary["last_scored_at"] if summary else None
        axes_scored = summary["axes_scored"] if summary else 0
        present_axes = scored_axes.get(server_id, set())
        missing_axes = [axis for axis in AXIS_NAMES if axis not in present_axes]

        if last_scored_at is None:
            age_hours = None
            stale = True
        else:
            age_hours = round(
                (now - _as_utc(last_scored_at)).total_seconds() / 3600.0,
                2,
            )
            stale = age_hours > max_age_hours

        if stale:
            stale_count += 1
        if axes_scored < len(AXIS_NAMES):
            incomplete_axis_count += 1

        items.append(
            {
                "server_id": server_id,
                "name": name or server_id,
                "last_scored_at": (
                    _isoformat(last_scored_at) if last_scored_at is not None else None
                ),
                "age_hours": age_hours,
                "axes_scored": axes_scored,
                "missing_axes": missing_axes,
                "risk_tier": risk_tier,
                "stale": stale,
            }
        )

    return {
        "generated_at": _isoformat(now),
        "max_age_hours": max_age_hours,
        "stale_count": stale_count,
        "total_servers": len(items),
        "incomplete_axis_count": incomplete_axis_count,
        "items": items[:limit],
    }


app = FastAPI(title="Score Freshness SLA")
app.include_router(router)


def run_self_test() -> int:
    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestSession = sessionmaker(bind=test_engine, autoflush=False, autocommit=False)

    McpServerRegistry.__table__.create(test_engine)
    McpLlmAxisScore.__table__.create(test_engine)

    def override_get_session() -> Iterator[Session]:
        db = TestSession()
        try:
            yield db
        finally:
            db.close()

    test_app = FastAPI()
    test_app.include_router(router)
    test_app.dependency_overrides[get_session] = override_get_session

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    old = now - timedelta(days=10)
    score_id = 1

    try:
        with TestSession() as db:
            db.add_all(
                [
                    McpServerRegistry(
                        server_id="fresh-complete",
                        name="Fresh Complete",
                        last_seen=now,
                        last_assessed=now,
                        risk_tier="low",
                        verdict="pass",
                    ),
                    McpServerRegistry(
                        server_id="fresh-incomplete",
                        name="Fresh Incomplete",
                        last_seen=now,
                        last_assessed=now,
                        risk_tier="medium",
                        verdict="review",
                    ),
                    McpServerRegistry(
                        server_id="stale-complete",
                        name="Stale Complete",
                        last_seen=old,
                        last_assessed=old,
                        risk_tier="high",
                        verdict="fail",
                    ),
                ]
            )

            scores = []
            for server_id, scored_at, axis_count in (
                ("fresh-complete", now, 7),
                ("fresh-incomplete", now, 2),
                ("stale-complete", old, 7),
            ):
                for axis_name in AXIS_NAMES[:axis_count]:
                    scores.append(
                        McpLlmAxisScore(
                            id=score_id,
                            server_id=server_id,
                            axis_name=axis_name,
                            scored_at=scored_at,
                            decision_rule_version="contract-v1",
                            model_version="contract-model",
                            p_top=0.1,
                        )
                    )
                    score_id += 1
            db.add_all(scores)
            db.commit()

        with TestClient(test_app) as client:
            response = client.get(
                "/api/scores/freshness",
                params={"max_age_hours": 168, "limit": 500},
            )
            assert response.status_code == 200, response.text
            payload = response.json()
            assert payload["stale_count"] == 1, payload
            assert payload["incomplete_axis_count"] == 1, payload

            incomplete = next(
                item
                for item in payload["items"]
                if item["server_id"] == "fresh-incomplete"
            )
            assert len(incomplete["missing_axes"]) == 5, incomplete
            assert set(incomplete["missing_axes"]) == set(AXIS_NAMES[2:]), incomplete

        print("PASS")
        return 0
    finally:
        test_engine.dispose()


def _self_test() -> int:
    return run_self_test()


if __name__ == "__main__":
    raise SystemExit(run_self_test())