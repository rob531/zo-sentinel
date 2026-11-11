from datetime import datetime, timezone
from statistics import fmean
from uuid import UUID

from fastapi import Depends, FastAPI
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore


CANONICAL_AXIS_NAMES = (
    "overall_risk",
    "auth_strength",
    "capability_breadth",
    "data_sensitivity",
    "network_egress",
    "maintainer_trust",
    "exploit_surface",
)

BAND_NAMES = ("low", "mid_low", "mid", "mid_high", "high")


def _band_for_p_top(p_top: float) -> str:
    if p_top < 0.2:
        return "low"
    if p_top < 0.4:
        return "mid_low"
    if p_top < 0.6:
        return "mid"
    if p_top < 0.8:
        return "mid_high"
    return "high"


def get_axis_ptop_band_distribution(session: Session = Depends(get_session)):
    ranked_scores = (
        select(
            McpLlmAxisScore.server_id,
            McpLlmAxisScore.axis_name,
            McpLlmAxisScore.p_top,
            McpLlmAxisScore.escalated,
            func.row_number()
            .over(
                partition_by=(
                    McpLlmAxisScore.server_id,
                    McpLlmAxisScore.axis_name,
                ),
                order_by=(
                    McpLlmAxisScore.scored_at.desc(),
                    McpLlmAxisScore.id.desc(),
                ),
            )
            .label("score_rank"),
        )
        .where(McpLlmAxisScore.axis_name.in_(CANONICAL_AXIS_NAMES))
        .subquery("ranked_axis_scores")
    )

    rows = session.execute(
        select(ranked_scores).where(ranked_scores.c.score_rank == 1)
    ).mappings()

    axis_data = {
        axis_name: {
            "axis_name": axis_name,
            "total": 0,
            "bands": {band_name: 0 for band_name in BAND_NAMES},
            "escalated": 0,
            "p_top_values": [],
        }
        for axis_name in CANONICAL_AXIS_NAMES
    }

    for row in rows:
        axis_name = row["axis_name"]
        if axis_name not in axis_data:
            continue

        entry = axis_data[axis_name]
        entry["total"] += 1

        p_top = row["p_top"]
        if p_top is not None:
            p_top = float(p_top)
            entry["bands"][_band_for_p_top(p_top)] += 1
            entry["p_top_values"].append(p_top)

        if bool(row["escalated"]):
            entry["escalated"] += 1

    axes = []
    bands_total = {band_name: 0 for band_name in BAND_NAMES}

    for axis_name in CANONICAL_AXIS_NAMES:
        entry = axis_data[axis_name]
        if entry["total"] == 0:
            continue

        for band_name in BAND_NAMES:
            bands_total[band_name] += entry["bands"][band_name]

        axes.append(
            {
                "axis_name": axis_name,
                "total": entry["total"],
                "bands": entry["bands"],
                "escalated": entry["escalated"],
                "mean_p_top": (
                    fmean(entry["p_top_values"])
                    if entry["p_top_values"]
                    else 0.0
                ),
            }
        )

    return {"axes": axes, "bands_total": bands_total}


get_ptop_band_distribution = get_axis_ptop_band_distribution
get_axis_band_distribution = get_axis_ptop_band_distribution
get_ptop_bands = get_axis_ptop_band_distribution
get_band_distribution = get_axis_ptop_band_distribution


if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    McpLlmAxisScore.__table__.create(test_engine)
    TestSession = sessionmaker(bind=test_engine)

    server_id_type = McpLlmAxisScore.__table__.c.server_id.type.python_type
    if issubclass(server_id_type, int):
        test_server_id = 1
    elif issubclass(server_id_type, UUID):
        test_server_id = UUID(int=1)
    else:
        test_server_id = "self-test-server"

    with TestSession() as session:
        session.add_all(
            [
                McpLlmAxisScore(
                    id=1,
                    server_id=test_server_id,
                    axis_name="overall_risk",
                    label="self-test",
                    label_index=0,
                    probs={},
                    p_top=0.1,
                    p_critical=0.01,
                    p_danger=0.02,
                    escalated=False,
                    escalated_to=None,
                    adapter_sha256="self-test",
                    decision_rule_version="self-test",
                    model_version="self-test",
                    scored_at=datetime(2025, 1, 1),
                ),
                McpLlmAxisScore(
                    id=2,
                    server_id=test_server_id,
                    axis_name="auth_strength",
                    label="self-test",
                    label_index=0,
                    probs={},
                    p_top=0.5,
                    p_critical=0.1,
                    p_danger=0.2,
                    escalated=False,
                    escalated_to=None,
                    adapter_sha256="self-test",
                    decision_rule_version="self-test",
                    model_version="self-test",
                    scored_at=datetime(2025, 1, 1),
                ),
                McpLlmAxisScore(
                    id=3,
                    server_id=test_server_id,
                    axis_name="data_sensitivity",
                    label="self-test",
                    label_index=0,
                    probs={},
                    p_top=0.9,
                    p_critical=0.5,
                    p_danger=0.7,
                    escalated=True,
                    escalated_to="overall_risk",
                    adapter_sha256="self-test",
                    decision_rule_version="self-test",
                    model_version="self-test",
                    scored_at=datetime(2025, 1, 1),
                ),
            ]
        )
        session.commit()

    def override_get_session():
        with TestSession() as session:
            yield session

    test_app = FastAPI()
    test_app.dependency_overrides[get_session] = override_get_session

    @test_app.get("/api/scoring/ptop-bands")
    def ptop_bands_endpoint(
        distribution=Depends(get_axis_ptop_band_distribution),
    ):
        return {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            **distribution,
        }

    response = TestClient(test_app).get("/api/scoring/ptop-bands")
    assert response.status_code == 200
    payload = response.json()
    assert {axis["axis_name"] for axis in payload["axes"]} == {
        "overall_risk",
        "auth_strength",
        "data_sensitivity",
    }
    high_axis = next(
        axis for axis in payload["axes"] if axis["axis_name"] == "data_sensitivity"
    )
    assert high_axis["bands"]["high"] == 1
    assert high_axis["escalated"] == 1
    print("PASS")
    test_engine.dispose()