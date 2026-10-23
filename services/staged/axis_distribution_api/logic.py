from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import text
from typing import Optional

from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry

router = APIRouter()


class Bucket(BaseModel):
    min: float
    max: float
    count: int


class AxisDistribution(BaseModel):
    axis_name: str
    label: str
    buckets: list[Bucket]


class DistributionResponse(BaseModel):
    axes: list[AxisDistribution]


def get_axis_distribution(tier: Optional[str] = None, session=None) -> list[AxisDistribution]:
    if tier:
        query = text("""
            SELECT
                a.axis_name,
                a.label,
                CASE
                    WHEN a.p_top >= 0.8 THEN 0
                    WHEN a.p_top >= 0.6 THEN 1
                    WHEN a.p_top >= 0.4 THEN 2
                    WHEN a.p_top >= 0.2 THEN 3
                    ELSE 4
                END as bucket_idx,
                CASE
                    WHEN a.p_top >= 0.8 THEN 0.8
                    WHEN a.p_top >= 0.6 THEN 0.6
                    WHEN a.p_top >= 0.4 THEN 0.4
                    WHEN a.p_top >= 0.2 THEN 0.2
                    ELSE 0.0
                END as min_val,
                CASE
                    WHEN a.p_top >= 0.8 THEN 1.0
                    WHEN a.p_top >= 0.6 THEN 0.8
                    WHEN a.p_top >= 0.4 THEN 0.6
                    WHEN a.p_top >= 0.2 THEN 0.4
                    ELSE 0.2
                END as max_val,
                COUNT(*) as cnt
            FROM mcp_llm_axis_scores a
            LEFT JOIN mcp_server_registry r ON a.server_id = r.server_id
            WHERE r.risk_tier = :tier
            GROUP BY a.axis_name, a.label, bucket_idx, min_val, max_val
            ORDER BY a.axis_name, min_val DESC
        """)
        result = session.execute(query, {"tier": tier})
    else:
        query = text("""
            SELECT
                a.axis_name,
                a.label,
                CASE
                    WHEN a.p_top >= 0.8 THEN 0
                    WHEN a.p_top >= 0.6 THEN 1
                    WHEN a.p_top >= 0.4 THEN 2
                    WHEN a.p_top >= 0.2 THEN 3
                    ELSE 4
                END as bucket_idx,
                CASE
                    WHEN a.p_top >= 0.8 THEN 0.8
                    WHEN a.p_top >= 0.6 THEN 0.6
                    WHEN a.p_top >= 0.4 THEN 0.4
                    WHEN a.p_top >= 0.2 THEN 0.2
                    ELSE 0.0
                END as min_val,
                CASE
                    WHEN a.p_top >= 0.8 THEN 1.0
                    WHEN a.p_top >= 0.6 THEN 0.8
                    WHEN a.p_top >= 0.4 THEN 0.6
                    WHEN a.p_top >= 0.2 THEN 0.4
                    ELSE 0.2
                END as max_val,
                COUNT(*) as cnt
            FROM mcp_llm_axis_scores a
            LEFT JOIN mcp_server_registry r ON a.server_id = r.server_id
            GROUP BY a.axis_name, a.label, bucket_idx, min_val, max_val
            ORDER BY a.axis_name, min_val DESC
        """)
        result = session.execute(query)

    axes_map = {}
    for row in result:
        axis_name = row[0]
        label = row[1]
        min_val = row[3]
        max_val = row[4]
        cnt = row[5]

        if axis_name not in axes_map:
            axes_map[axis_name] = {"axis_name": axis_name, "label": label, "buckets": []}

        axes_map[axis_name]["buckets"].append(Bucket(min=min_val, max=max_val, count=cnt))

    return [AxisDistribution(**v) for v in axes_map.values()]


@router.get("/api/axis/distribution", response_model=DistributionResponse)
def axis_distribution(tier: Optional[str] = None, session=Depends(get_session)) -> DistributionResponse:
    axes = get_axis_distribution(tier=tier, session=session)
    return DistributionResponse(axes=axes)


if __name__ == "__main__":
    from fastapi import FastAPI
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker, Session
    from sqlalchemy.pool import StaticPool

    app = FastAPI()
    app.include_router(router)

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    metadata_proxy = type("Meta", (), {"tables": []})()

    from sqlalchemy import MetaData, Table, Column, String, Float, Integer
    m = MetaData()

    servers_table = Table(
        "mcp_server_registry",
        m,
        Column("server_id", String, primary_key=True),
        Column("name", String),
        Column("risk_tier", String),
    )

    axis_scores_table = Table(
        "mcp_llm_axis_scores",
        m,
        Column("id", Integer, primary_key=True, autoincrement=True),
        Column("server_id", String),
        Column("axis_name", String),
        Column("label", String),
        Column("p_top", Float),
    )

    m.create_all(engine)

    TestingSession = sessionmaker(bind=engine)
    test_session = TestingSession()

    test_session.execute(servers_table.insert().values(server_id="srv1", name="Server1", risk_tier="TRUSTED_GENERAL"))
    test_session.execute(servers_table.insert().values(server_id="srv2", name="Server2", risk_tier="TRUSTED_GENERAL"))
    test_session.execute(servers_table.insert().values(server_id="srv3", name="Server3", risk_tier="HIGH_RISK_ISOLATED"))
    test_session.execute(servers_table.insert().values(server_id="srv4", name="Server4", risk_tier="HIGH_RISK_ISOLATED"))

    axis_data = [
        ("overall_risk", "high", 0.85),
        ("overall_risk", "high", 0.72),
        ("overall_risk", "high", 0.91),
        ("overall_risk", "medium", 0.45),
        ("overall_risk", "medium", 0.55),
        ("auth_strength", "low", 0.15),
        ("auth_strength", "low", 0.25),
        ("auth_strength", "medium", 0.52),
        ("auth_strength", "medium", 0.48),
        ("auth_strength", "high", 0.88),
        ("capability_breadth", "high", 0.78),
        ("capability_breadth", "high", 0.82),
        ("capability_breadth", "medium", 0.58),
        ("capability_breadth", "medium", 0.62),
        ("capability_breadth", "low", 0.12),
        ("data_sensitivity", "critical", 0.92),
        ("data_sensitivity", "critical", 0.88),
        ("data_sensitivity", "high", 0.74),
        ("data_sensitivity", "medium", 0.51),
        ("data_sensitivity", "medium", 0.43),
    ]

    for axis_name, label, p_top in axis_data:
        server_id = f"srv{(axis_data.index((axis_name, label, p_top)) % 4) + 1}"
        test_session.execute(
            axis_scores_table.insert().values(
                server_id=server_id, axis_name=axis_name, label=label, p_top=p_top
            )
        )

    test_session.commit()

    def override_get_session():
        yield test_session

    app.dependency_overrides[get_session] = override_get_session

    from fastapi.testclient import TestClient
    client = TestClient(app)

    resp_all = client.get("/api/axis/distribution")
    assert resp_all.status_code == 200, f"Expected 200, got {resp_all.status_code}"
    data_all = resp_all.json()
    assert len(data_all["axes"]) == 4, f"Expected 4 axes, got {len(data_all['axes'])}"
    total_buckets = sum(len(ax["buckets"]) for ax in data_all["axes"])
    assert total_buckets > 0, "Expected non-zero bucket counts"

    resp_trusted = client.get("/api/axis/distribution?tier=TRUSTED_GENERAL")
    assert resp_trusted.status_code == 200, f"Expected 200, got {resp_trusted.status_code}"
    data_trusted = resp_trusted.json()
    assert len(data_trusted["axes"]) == 4, f"Expected 4 axes for TRUSTED_GENERAL, got {len(data_trusted['axes'])}"

    print("PASS")