from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Dict, List, Optional

from fastapi import APIRouter, Depends, FastAPI, Query
from fastapi.testclient import TestClient
from pydantic import BaseModel
from sqlalchemy import create_engine, insert, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import McpLlmAxisScore


class LegalAxisName(str, Enum):
    overall_risk = "overall_risk"
    auth_strength = "auth_strength"
    capability_breadth = "capability_breadth"
    data_sensitivity = "data_sensitivity"
    network_egress = "network_egress"
    maintainer_trust = "maintainer_trust"
    exploit_surface = "exploit_surface"


class ConfidenceBand(BaseModel):
    band: str
    min_p: float
    max_p: float
    count: int
    pct: float


class AxisConfidenceBands(BaseModel):
    bands: List[ConfidenceBand]
    count: int


class ConfidenceBandsResponse(BaseModel):
    bands: List[ConfidenceBand]
    by_axis: Dict[str, AxisConfidenceBands]
    total_rows: int


_BANDS = (
    ("[0.0,0.2)", 0.0, 0.2),
    ("[0.2,0.4)", 0.2, 0.4),
    ("[0.4,0.6)", 0.4, 0.6),
    ("[0.6,0.8)", 0.6, 0.8),
    ("[0.8,1.0]", 0.8, 1.0),
)


def _band_index(p_top: float) -> int:
    value = float(p_top)
    for index, (_, minimum, maximum) in enumerate(_BANDS):
        if value < minimum:
            continue
        if value < maximum or (index == len(_BANDS) - 1 and value <= maximum):
            return index
    return len(_BANDS) - 1


def _band_rows(counts: List[int], total: int) -> List[Dict[str, object]]:
    return [
        {
            "band": label,
            "min_p": minimum,
            "max_p": maximum,
            "count": counts[index],
            "pct": (counts[index] * 100.0 / total) if total else 0.0,
        }
        for index, (label, minimum, maximum) in enumerate(_BANDS)
    ]


def get_confidence_bands(
    session: Session,
    axis_name: Optional[LegalAxisName] = None,
) -> Dict[str, object]:
    statement = select(
        McpLlmAxisScore.server_id,
        McpLlmAxisScore.axis_name,
        McpLlmAxisScore.p_top,
        McpLlmAxisScore.p_critical,
        McpLlmAxisScore.p_danger,
        McpLlmAxisScore.escalated,
        McpLlmAxisScore.scored_at,
    )
    if axis_name is not None:
        statement = statement.where(McpLlmAxisScore.axis_name == axis_name.value)

    rows = session.execute(statement).all()
    global_counts = [0] * len(_BANDS)
    axis_counts: Dict[str, List[int]] = {}

    for row in rows:
        name = str(row.axis_name)
        counts = axis_counts.setdefault(name, [0] * len(_BANDS))
        index = _band_index(row.p_top)
        counts[index] += 1
        global_counts[index] += 1

    total_rows = len(rows)
    by_axis: Dict[str, Dict[str, object]] = {}
    for name in sorted(axis_counts):
        counts = axis_counts[name]
        axis_total = sum(counts)
        by_axis[name] = {
            "bands": _band_rows(counts, axis_total),
            "count": axis_total,
        }

    return {
        "bands": _band_rows(global_counts, total_rows),
        "by_axis": by_axis,
        "total_rows": total_rows,
    }


router = APIRouter(prefix="/api")


@router.get("/axis/confidence-bands", response_model=ConfidenceBandsResponse)
def confidence_bands(
    axis_name: Optional[LegalAxisName] = Query(default=None),
    session: Session = Depends(get_session),
) -> Dict[str, object]:
    return get_confidence_bands(session, axis_name)


def run_self_test() -> None:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    test_session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    with engine.begin() as connection:
        connection.exec_driver_sql(
            """
            CREATE TABLE mcp_llm_axis_scores (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                server_id VARCHAR(255) NOT NULL,
                axis_name VARCHAR(64) NOT NULL,
                p_top FLOAT NOT NULL,
                p_critical FLOAT,
                p_danger FLOAT,
                escalated BOOLEAN NOT NULL DEFAULT 0,
                scored_at DATETIME NOT NULL
            )
            """
        )

    seeded_rows = [
        {
            "server_id": "server-overall-risk",
            "axis_name": LegalAxisName.overall_risk.value,
            "p_top": 0.05,
            "p_critical": 0.01,
            "p_danger": 0.02,
            "escalated": False,
            "scored_at": datetime(2024, 1, 1, tzinfo=timezone.utc),
        },
        {
            "server_id": "server-auth-strength",
            "axis_name": LegalAxisName.auth_strength.value,
            "p_top": 0.5,
            "p_critical": 0.4,
            "p_danger": 0.45,
            "escalated": False,
            "scored_at": datetime(2024, 1, 2, tzinfo=timezone.utc),
        },
        {
            "server_id": "server-capability-breadth",
            "axis_name": LegalAxisName.capability_breadth.value,
            "p_top": 0.95,
            "p_critical": 0.9,
            "p_danger": 0.92,
            "escalated": True,
            "scored_at": datetime(2024, 1, 3, tzinfo=timezone.utc),
        },
    ]

    with test_session_factory() as session:
        session.execute(insert(McpLlmAxisScore), seeded_rows)
        session.commit()

    def override_get_session():
        with test_session_factory() as session:
            yield session

    test_app = FastAPI()
    test_app.include_router(router)
    test_app.dependency_overrides[get_session] = override_get_session

    with TestClient(test_app) as client:
        response = client.get("/api/axis/confidence-bands")
        assert response.status_code == 200, response.text
        payload = response.json()

        expected_bands = [(0.0, 0.2), (0.2, 0.4), (0.4, 0.6), (0.6, 0.8), (0.8, 1.0)]
        assert len(payload["bands"]) == 5
        assert [(band["min_p"], band["max_p"]) for band in payload["bands"]] == expected_bands

        high_band = next(
            band for band in payload["bands"] if band["min_p"] == 0.8 and band["max_p"] == 1.0
        )
        expected_high_count = sum(1 for row in seeded_rows if row["p_top"] >= 0.8)
        assert high_band["count"] == expected_high_count
        assert payload["total_rows"] == len(seeded_rows)

        assert set(payload["by_axis"]) == {
            LegalAxisName.overall_risk.value,
            LegalAxisName.auth_strength.value,
            LegalAxisName.capability_breadth.value,
        }
        for axis_payload in payload["by_axis"].values():
            assert axis_payload["count"] == sum(band["count"] for band in axis_payload["bands"])
            assert len(axis_payload["bands"]) == 5

        filtered = client.get(
            "/api/axis/confidence-bands",
            params={"axis_name": LegalAxisName.capability_breadth.value},
        )
        assert filtered.status_code == 200, filtered.text
        filtered_payload = filtered.json()
        assert filtered_payload["total_rows"] == 1
        assert set(filtered_payload["by_axis"]) == {LegalAxisName.capability_breadth.value}
        assert filtered_payload["by_axis"][LegalAxisName.capability_breadth.value]["bands"][4]["count"] == 1

        invalid = client.get(
            "/api/axis/confidence-bands",
            params={"axis_name": "not_a_legal_axis"},
        )
        assert invalid.status_code == 422

    engine.dispose()
    print("PASS")


if __name__ == "__main__":
    run_self_test()