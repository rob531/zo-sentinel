from __future__ import annotations

import csv
import io
import sys
from datetime import datetime, timedelta

from app.db import get_session
from app.models import Base, McpLlmAxisScore, McpServerRegistry


CSV_COLUMNS = [
    "server_id",
    "name",
    "url",
    "registry_source",
    "risk_tier",
    "verdict",
    "trust_score",
    "last_scanned",
    "overall_risk_p_top",
    "description",
]


def run() -> bool:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from .router import router

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(
        bind=engine,
        tables=[McpServerRegistry.__table__, McpLlmAxisScore.__table__],
    )
    TestingSessionLocal = sessionmaker(
        bind=engine,
        autoflush=False,
        autocommit=False,
    )

    tiers = [
        "HIGH_RISK_ISOLATED",
        "MEDIUM_RISK",
        "LOW_RISK",
        "HIGH_RISK_CONNECTED",
        "HIGH_RISK_ISOLATED",
        "TRUSTED",
        "MEDIUM_RISK",
        "LOW_RISK",
        "HIGH_RISK_ISOLATED",
        "HIGH_RISK_CONNECTED",
    ]
    sources = ["community", "enterprise", "verified", "internal"]
    expected_server_ids = {
        f"srv-{index:03d}"
        for index, tier in enumerate(tiers)
        if tier == "HIGH_RISK_ISOLATED"
    }

    with TestingSessionLocal() as db:
        for index, tier in enumerate(tiers):
            server_id = f"srv-{index:03d}"
            db.add(
                McpServerRegistry(
                    server_id=server_id,
                    name=f"Server {index:03d}",
                    url=f"https://server-{index:03d}.example.test",
                    registry_source=sources[index % len(sources)],
                    risk_tier=tier,
                    verdict="REVIEW_REQUIRED" if "HIGH_RISK" in tier else "APPROVED",
                    trust_score=0.25 + index * 0.05,
                    last_scanned=datetime(2025, 1, 1) + timedelta(days=index),
                    description=f"Security audit server {index:03d}",
                )
            )
            db.add(
                McpLlmAxisScore(
                    id=index + 1,
                    server_id=server_id,
                    axis_name="overall_risk",
                    label="HIGH" if "HIGH_RISK" in tier else "LOW",
                    label_index=1 if "HIGH_RISK" in tier else 0,
                    p_top=0.9 if "HIGH_RISK" in tier else 0.1,
                    model_version="contract-test-v1",
                    scored_at=datetime(2025, 1, 1) + timedelta(days=index),
                )
            )
        db.commit()

    def override_get_session():
        db = TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()

    test_app = FastAPI()
    test_app.include_router(router)
    test_app.dependency_overrides[get_session] = override_get_session

    try:
        with TestClient(test_app) as client:
            response = client.get(
                "/api/search/export",
                params={"risk_tier": "HIGH_RISK_ISOLATED"},
            )
        assert response.status_code == 200, response.text
        assert response.headers.get("content-type", "").startswith("text/csv"), response.headers
        assert "attachment" in response.headers.get("content-disposition", ""), response.headers

        reader = csv.DictReader(io.StringIO(response.text))
        rows = list(reader)
        assert reader.fieldnames is not None
        assert set(CSV_COLUMNS).issubset(reader.fieldnames), reader.fieldnames
        assert len(rows) == len(expected_server_ids), rows
        assert {row["server_id"] for row in rows} == expected_server_ids, rows
        assert all(row["risk_tier"] == "HIGH_RISK_ISOLATED" for row in rows), rows
        assert all(row["overall_risk_p_top"] == "0.9" for row in rows), rows
    finally:
        engine.dispose()

    return True


if __name__ == "__main__":
    try:
        run()
    except Exception as exc:
        print("FAIL: %r" % (exc,))
        sys.exit(1)
    print("PASS")
    sys.exit(0)