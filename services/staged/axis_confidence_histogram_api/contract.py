"""Acceptance self-test for the axis confidence histogram API."""
from __future__ import annotations

import sys


def run() -> bool:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.db import get_session
    from app.models import Base, McpLlmAxisScore

    from .router import router

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    testing_session = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    session = testing_session()
    try:
        session.add_all(
            [
                McpLlmAxisScore(
                    id=1,
                    server_id="srv-contract",
                    axis_name="overall_risk",
                    model_version="contract-test",
                    p_top=5.0,
                ),
                McpLlmAxisScore(
                    id=2,
                    server_id="srv-contract",
                    axis_name="auth_strength",
                    model_version="contract-test",
                    p_top=25.0,
                ),
                McpLlmAxisScore(
                    id=3,
                    server_id="srv-contract",
                    axis_name="capability_breadth",
                    model_version="contract-test",
                    p_top=75.0,
                ),
                McpLlmAxisScore(
                    id=4,
                    server_id="srv-contract",
                    axis_name="data_sensitivity",
                    model_version="contract-test",
                    p_top=None,
                ),
                McpLlmAxisScore(
                    id=5,
                    server_id="srv-contract",
                    axis_name="network_egress",
                    model_version="contract-test",
                    p_top=95.0,
                ),
            ]
        )
        session.commit()
    finally:
        session.close()

    def override_get_session():
        db = testing_session()
        try:
            yield db
        finally:
            db.close()

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = override_get_session

    client = TestClient(app)
    response = client.get("/api/axes/confidence-histogram")
    assert response.status_code == 200, response.text

    body = response.json()
    assert "generated_at" in body, body
    assert isinstance(body.get("axes"), list), body

    expected_axes = {
        "overall_risk",
        "auth_strength",
        "capability_breadth",
        "data_sensitivity",
        "network_egress",
        "maintainer_trust",
        "exploit_surface",
    }
    axes = body["axes"]
    assert len(axes) == 7, body
    assert {axis["axis_name"] for axis in axes} == expected_axes, body

    unknown_count = 0
    total_rows = 0
    for axis in axes:
        axis_total = axis["total"]
        bucket_total = sum(bucket["count"] for bucket in axis["buckets"])
        assert bucket_total == axis_total, axis
        total_rows += axis_total
        unknown_count += sum(
            bucket["count"]
            for bucket in axis["buckets"]
            if bucket["label"] == "unknown"
        )

    assert unknown_count == 1, body
    assert total_rows == 5, body
    return True


if __name__ == "__main__":
    try:
        run()
    except Exception as exc:  # noqa: BLE001
        print("FAIL: %r" % (exc,))
        sys.exit(1)
    print("PASS")
    sys.exit(0)