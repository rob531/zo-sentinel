import math
from datetime import date, datetime, timezone
from typing import Any, Optional

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry

router = APIRouter(prefix="/api", tags=["registry"])


def _iso8601(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.isoformat()
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day, tzinfo=timezone.utc).isoformat()
    return str(value)


def _utc_now_iso8601() -> str:
    return datetime.now(timezone.utc).isoformat()


def _as_count(value: Any) -> int:
    return int(value or 0)


def _average_trust_score(values: list[Any]) -> Optional[float]:
    numeric_values = []
    for value in values:
        if value is None:
            continue
        try:
            numeric_value = float(value)
        except (TypeError, ValueError):
            continue
        if math.isfinite(numeric_value):
            numeric_values.append(numeric_value)
    if not numeric_values:
        return None
    return sum(numeric_values) / len(numeric_values)


@router.get("/registry/ingest-lanes")
def get_registry_ingest_lanes(session: Session = Depends(get_session)) -> dict[str, Any]:
    rows = session.execute(
        select(
            McpServerRegistry.registry_source,
            McpServerRegistry.verdict,
            McpServerRegistry.risk_tier,
            McpServerRegistry.trust_score,
            McpServerRegistry.last_seen,
        )
    ).all()

    lanes_by_source: dict[Any, dict[str, Any]] = {}

    for row in rows:
        source = row.registry_source
        lane = lanes_by_source.setdefault(
            source,
            {
                "server_count": 0,
                "by_verdict": {},
                "by_risk_tier": {},
                "trust_scores": [],
                "last_seen": None,
            },
        )
        lane["server_count"] += 1

        if row.verdict is not None:
            verdict = str(row.verdict)
            lane["by_verdict"][verdict] = lane["by_verdict"].get(verdict, 0) + 1

        if row.risk_tier is not None:
            risk_tier = str(row.risk_tier)
            lane["by_risk_tier"][risk_tier] = lane["by_risk_tier"].get(risk_tier, 0) + 1

        if row.trust_score is not None:
            lane["trust_scores"].append(row.trust_score)

        current_last_seen = _iso8601(row.last_seen)
        if current_last_seen is not None:
            previous_last_seen = lane["last_seen"]
            if previous_last_seen is None or current_last_seen > previous_last_seen:
                lane["last_seen"] = current_last_seen

    total_servers = len(rows)
    lanes = []
    for source, lane in lanes_by_source.items():
        server_count = _as_count(lane["server_count"])
        lanes.append(
            {
                "registry_source": source,
                "server_count": server_count,
                "share": round(server_count / total_servers, 4) if total_servers else 0.0,
                "by_verdict": lane["by_verdict"],
                "by_risk_tier": lane["by_risk_tier"],
                "avg_trust_score": _average_trust_score(lane["trust_scores"]),
                "last_seen": lane["last_seen"],
            }
        )

    lanes.sort(key=lambda lane: (-lane["server_count"], str(lane["registry_source"])))
    return {
        "total_servers": total_servers,
        "lanes": lanes,
        "generated_at": _utc_now_iso8601(),
    }


if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    McpServerRegistry.__table__.create(test_engine)
    TestSession = sessionmaker(bind=test_engine)

    test_app = FastAPI()
    test_app.include_router(router)

    def override_get_session():
        session = TestSession()
        try:
            yield session
        finally:
            session.close()

    test_app.dependency_overrides[get_session] = override_get_session

    with TestSession() as session:
        session.add_all(
            [
                McpServerRegistry(
                    server_id="server-1",
                    name="Server One",
                    registry_source="smithery",
                    trust_score=0.9,
                    verdict="allow",
                    risk_tier="low",
                    last_seen=datetime(2025, 1, 1, tzinfo=timezone.utc),
                ),
                McpServerRegistry(
                    server_id="server-2",
                    name="Server Two",
                    registry_source="smithery",
                    trust_score=0.7,
                    verdict="allow",
                    risk_tier="medium",
                    last_seen=datetime(2025, 1, 2, tzinfo=timezone.utc),
                ),
                McpServerRegistry(
                    server_id="server-3",
                    name="Server Three",
                    registry_source="smithery",
                    trust_score=0.5,
                    verdict="deny",
                    risk_tier="high",
                    last_seen=datetime(2025, 1, 3, tzinfo=timezone.utc),
                ),
                McpServerRegistry(
                    server_id="server-4",
                    name="Server Four",
                    registry_source="internal",
                    trust_score=None,
                    verdict="deny",
                    risk_tier="medium",
                    last_seen=datetime(2025, 1, 4, tzinfo=timezone.utc),
                ),
            ]
        )
        session.commit()

    response = TestClient(test_app).get("/api/registry/ingest-lanes")
    assert response.status_code == 200
    payload = response.json()
    assert payload["total_servers"] == 4
    assert len(payload["lanes"]) == 2
    assert payload["lanes"][0]["registry_source"] == "smithery"
    assert payload["lanes"][0]["server_count"] == 3
    assert payload["lanes"][0]["share"] == 0.75
    assert sum(payload["lanes"][0]["by_verdict"].values()) == payload["lanes"][0]["server_count"]
    assert sum(payload["lanes"][0]["by_risk_tier"].values()) == payload["lanes"][0]["server_count"]
    assert payload["lanes"][1]["server_count"] == 1
    assert payload["lanes"][1]["share"] == 0.25
    assert payload["lanes"][1]["avg_trust_score"] is None
    print("PASS")