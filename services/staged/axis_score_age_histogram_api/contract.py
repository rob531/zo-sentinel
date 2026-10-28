from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional

from fastapi import APIRouter, Depends, FastAPI, Query
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry


router = APIRouter()
app = FastAPI()
app.include_router(router)

SCORE_TABLE = McpLlmAxisScore.__tablename__
SERVER_TABLE = McpServerRegistry.__tablename__

BANDS = ["<1d", "1-7d", "7-30d", "30-90d", ">90d", "NEVER_SCORED"]


def _band_for_age(age_days: int) -> str:
    if age_days < 1:
        return "<1d"
    if age_days < 7:
        return "1-7d"
    if age_days < 30:
        return "7-30d"
    if age_days <= 90:
        return "30-90d"
    return ">90d"


def _as_datetime(value: Any) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))


def _histogram(session: Session, axis_name: Optional[str], days: int) -> Dict[str, Any]:
    server_rows = session.execute(
        text(f"SELECT server_id FROM {SERVER_TABLE}")
    ).mappings().all()
    servers = [row["server_id"] for row in server_rows]

    score_rows = session.execute(
        text(
            f"""
            SELECT server_id, axis_name, scored_at
            FROM {SCORE_TABLE}
            WHERE (:axis_name IS NULL OR axis_name = :axis_name)
            """
        ),
        {"axis_name": axis_name},
    ).mappings().all()

    latest: Dict[tuple, datetime] = {}
    axes: List[str] = []

    for row in score_rows:
        current_axis = row["axis_name"]
        if current_axis not in axes:
            axes.append(current_axis)

        scored_at = _as_datetime(row["scored_at"])
        if scored_at is None:
            continue

        key = (row["server_id"], current_axis)
        if key not in latest or scored_at > latest[key]:
            latest[key] = scored_at

    if axis_name is not None:
        axes = [axis_name]

    today = datetime.now(timezone.utc).date()
    result_axes: List[Dict[str, Any]] = []

    for current_axis in axes:
        counts = {band: 0 for band in BANDS}
        for server_id in servers:
            score_time = latest.get((server_id, current_axis))
            if score_time is None:
                counts["NEVER_SCORED"] += 1
                continue

            score_date = score_time.date()
            age_days = (today - score_date).days
            counts[_band_for_age(age_days)] += 1

        result_axes.append(
            {
                "axis_name": current_axis,
                "total_servers": len(servers),
                "bands": [
                    {"band": band, "count": counts[band]}
                    for band in BANDS
                ],
            }
        )

    return {"days": days, "axes": result_axes}


@router.get("/api/axis/score-age-histogram")
def score_age_histogram(
    axis_name: Optional[str] = Query(default=None),
    days: int = Query(default=30),
    session: Session = Depends(get_session),
) -> Dict[str, Any]:
    return _histogram(session, axis_name, days)


get_score_age_histogram = score_age_histogram


def _run_self_test() -> None:
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestingSession = sessionmaker(bind=engine, autocommit=False, autoflush=False)

    with engine.begin() as connection:
        connection.execute(
            text(
                f"""
                CREATE TABLE {SERVER_TABLE} (
                    server_id TEXT PRIMARY KEY
                )
                """
            )
        )
        connection.execute(
            text(
                f"""
                CREATE TABLE {SCORE_TABLE} (
                    id INTEGER PRIMARY KEY,
                    server_id TEXT,
                    axis_name TEXT,
                    scored_at TIMESTAMP,
                    p_top REAL,
                    escalated INTEGER
                )
                """
            )
        )

    now = datetime.now(timezone.utc)
    server_ids = ["server-0", "server-2", "server-20", "server-60", "server-200", "server-never"]

    with TestingSession() as session:
        for server_id in server_ids:
            session.execute(
                text(f"INSERT INTO {SERVER_TABLE} (server_id) VALUES (:server_id)"),
                {"server_id": server_id},
            )

        for server_id, age in [
            ("server-0", 0),
            ("server-2", 2),
            ("server-20", 20),
            ("server-60", 60),
            ("server-200", 200),
        ]:
            session.execute(
                text(
                    f"""
                    INSERT INTO {SCORE_TABLE}
                        (server_id, axis_name, scored_at, p_top, escalated)
                    VALUES
                        (:server_id, :axis_name, :scored_at, :p_top, :escalated)
                    """
                ),
                {
                    "server_id": server_id,
                    "axis_name": "risk",
                    "scored_at": now - timedelta(days=age),
                    "p_top": 0.9,
                    "escalated": 0,
                },
            )
        session.commit()

    def override_get_session():
        with TestingSession() as session:
            yield session

    that_app = FastAPI()
    that_app.include_router(router)
    that_app.dependency_overrides[get_session] = override_get_session

    with TestClient(that_app) as client:
        response = client.get(
            "/api/axis/score-age-histogram",
            params={"axis_name": "risk", "days": 365},
        )

        assert response.status_code == 200
        payload = response.json()
        assert payload["days"] == 365
        assert len(payload["axes"]) == 1

        axis = payload["axes"][0]
        assert axis["axis_name"] == "risk"
        assert axis["total_servers"] == 6

        counts = {item["band"]: item["count"] for item in axis["bands"]}
        assert counts["NEVER_SCORED"] == 1
        assert counts["1-7d"] == 1
        assert counts["7-30d"] == 1
        assert counts["30-90d"] == 1
        assert counts[">90d"] == 1
        assert counts["<1d"] == 1
        assert len(axis["bands"]) == len(BANDS)

    print("PASS")


if __name__ == "__main__":
    _run_self_test()