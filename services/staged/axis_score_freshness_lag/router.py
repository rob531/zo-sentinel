from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends

from app.db import get_session
from app.models import McpLlmAxisScore

router = APIRouter(prefix="/api")

VALID_AXIS_NAMES = (
    "overall_risk",
    "auth_strength",
    "capability_breadth",
    "data_sensitivity",
    "network_egress",
    "maintainer_trust",
    "exploit_surface",
)
_VALID_AXIS_SET = set(VALID_AXIS_NAMES)


def _as_utc(value: Any) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, datetime):
        parsed = value
    else:
        text = str(value).strip()
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


def _iso(value: Optional[datetime]) -> Optional[str]:
    return value.isoformat() if value is not None else None


def _lag_seconds(scored_at: datetime, as_of: datetime) -> int:
    return max(0, int((as_of - scored_at).total_seconds()))


@router.get("/axis/freshness-lag")
def get_axis_score_freshness_lag(
    axis_name: Optional[str] = None,
    session=Depends(get_session),
) -> Dict[str, Any]:
    if axis_name is not None and axis_name not in _VALID_AXIS_SET:
        return {"as_of": datetime.now(timezone.utc).isoformat(), "axes": []}

    selected_axes = (
        (axis_name,) if axis_name is not None else VALID_AXIS_NAMES
    )
    rows = (
        session.query(
            McpLlmAxisScore.axis_name,
            McpLlmAxisScore.scored_at,
            McpLlmAxisScore.server_id,
            McpLlmAxisScore.p_top,
        )
        .filter(McpLlmAxisScore.axis_name.in_(selected_axes))
        .all()
    )

    as_of = datetime.now(timezone.utc)
    stale_24h_cutoff = as_of - timedelta(hours=24)
    stale_7d_cutoff = as_of - timedelta(days=7)
    grouped: Dict[str, List[datetime]] = {}
    counts: Dict[str, int] = {}

    for row in rows:
        name = row.axis_name
        if name not in _VALID_AXIS_SET:
            continue
        counts[name] = counts.get(name, 0) + 1
        scored_at = _as_utc(row.scored_at)
        if scored_at is not None:
            grouped.setdefault(name, []).append(scored_at)

    axes: List[Dict[str, Any]] = []
    for name in VALID_AXIS_NAMES:
        if name not in counts:
            continue
        scored_values = grouped.get(name, [])
        if scored_values:
            oldest = min(scored_values)
            newest = max(scored_values)
            lags = sorted(_lag_seconds(value, as_of) for value in scored_values)
            middle = len(lags) // 2
            if len(lags) % 2:
                lag_p50 = int(lags[middle])
            else:
                lag_p50 = int((lags[middle - 1] + lags[middle]) / 2)
            stale_24h_count = sum(value <= stale_24h_cutoff for value in scored_values)
            stale_7d_count = sum(value <= stale_7d_cutoff for value in scored_values)
        else:
            oldest = None
            newest = None
            lag_p50 = None
            stale_24h_count = 0
            stale_7d_count = 0

        axes.append(
            {
                "axis_name": name,
                "oldest_scored_at": _iso(oldest),
                "newest_scored_at": _iso(newest),
                "scored_count": int(counts[name]),
                "stale_24h_count": int(stale_24h_count),
                "stale_7d_count": int(stale_7d_count),
                "lag_seconds_p50": lag_p50,
            }
        )

    return {"as_of": as_of.isoformat(), "axes": axes}


if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SessionLocal = sessionmaker(bind=engine)

    with engine.begin() as connection:
        connection.exec_driver_sql(
            """
            CREATE TABLE mcp_llm_axis_scores (
                id INTEGER PRIMARY KEY,
                axis_name VARCHAR(255) NOT NULL,
                scored_at DATETIME,
                server_id VARCHAR(255) NOT NULL,
                p_top FLOAT
            )
            """
        )
        now = datetime.now(timezone.utc)
        seed_rows = []
        for axis in ("overall_risk", "auth_strength"):
            for age, server_id in (
                (timedelta(0), "server-now"),
                (timedelta(days=2), "server-two-days"),
                (timedelta(days=30), "server-thirty-days"),
            ):
                seed_rows.append(
                    {
                        "axis_name": axis,
                        "scored_at": (now - age).isoformat(),
                        "server_id": server_id,
                        "p_top": 0.5,
                    }
                )
        for row in seed_rows:
            connection.exec_driver_sql(
                """
                INSERT INTO mcp_llm_axis_scores
                    (axis_name, scored_at, server_id, p_top)
                VALUES
                    (:axis_name, :scored_at, :server_id, :p_top)
                """,
                row,
            )

    def override_get_session():
        db = SessionLocal()
        try:
            yield db
        finally:
            db.close()

    that_app = FastAPI()
    that_app.include_router(router)
    that_app.dependency_overrides[get_session] = override_get_session

    client = TestClient(that_app)
    response = client.get("/api/axis/freshness-lag")
    assert response.status_code == 200, response.text
    payload = response.json()
    assert {item["axis_name"] for item in payload["axes"]} >= {
        "overall_risk",
        "auth_strength",
    }
    thirty_day_axis = next(
        item for item in payload["axes"] if item["axis_name"] == "overall_risk"
    )
    assert thirty_day_axis["stale_7d_count"] >= 1
    assert thirty_day_axis["lag_seconds_p50"] is None or isinstance(
        thirty_day_axis["lag_seconds_p50"], int
    )
    print("PASS")