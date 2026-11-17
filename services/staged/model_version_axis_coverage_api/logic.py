from __future__ import annotations

import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parents[3]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from fastapi import Depends
from sqlalchemy import case, distinct, func, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore


EXPECTED_AXIS_COUNT = 7
EXPECTED_AXES = (
    "overall_risk",
    "auth_strength",
    "capability_breadth",
    "data_sensitivity",
    "network_egress",
    "maintainer_trust",
    "exploit_surface",
)


def get_model_version_axis_coverage(
    session: Session,
    incomplete: bool = False,
) -> list[dict[str, Any]]:
    grouped = session.execute(
        select(
            McpLlmAxisScore.model_version.label("model_version"),
            func.count(distinct(McpLlmAxisScore.server_id)).label("servers"),
            func.count(distinct(McpLlmAxisScore.axis_name)).label("axis_coverage"),
            func.sum(
                case((McpLlmAxisScore.escalated.is_(True), 1), else_=0)
            ).label("escalated_rows"),
            func.min(McpLlmAxisScore.scored_at).label("first_scored_at"),
            func.max(McpLlmAxisScore.scored_at).label("last_scored_at"),
        )
        .where(McpLlmAxisScore.model_version.is_not(None))
        .group_by(McpLlmAxisScore.model_version)
        .order_by(McpLlmAxisScore.model_version)
    ).all()

    axis_rows = session.execute(
        select(
            distinct(McpLlmAxisScore.model_version),
            McpLlmAxisScore.axis_name,
        )
        .where(
            McpLlmAxisScore.model_version.is_not(None),
            McpLlmAxisScore.axis_name.is_not(None),
        )
        .order_by(McpLlmAxisScore.model_version, McpLlmAxisScore.axis_name)
    ).all()

    axes_by_version: defaultdict[str, set[str]] = defaultdict(set)
    for model_version, axis_name in axis_rows:
        axes_by_version[model_version].add(axis_name)

    result: list[dict[str, Any]] = []
    for row in grouped:
        axis_names = sorted(axes_by_version[row.model_version])
        axis_coverage = int(row.axis_coverage or 0)
        if incomplete and axis_coverage >= EXPECTED_AXIS_COUNT:
            continue
        result.append(
            {
                "model_version": row.model_version,
                "servers": int(row.servers or 0),
                "axis_coverage": axis_coverage,
                "axis_names": axis_names,
                "escalated_rows": int(row.escalated_rows or 0),
                "first_scored_at": row.first_scored_at,
                "last_scored_at": row.last_scored_at,
            }
        )

    return result


def get_model_versions(
    session: Session,
    incomplete: bool = False,
) -> list[dict[str, Any]]:
    return get_model_version_axis_coverage(session, incomplete=incomplete)


def get_axis_coverage(
    session: Session,
    incomplete: bool = False,
) -> list[dict[str, Any]]:
    return get_model_version_axis_coverage(session, incomplete=incomplete)


def get_model_version_coverage(
    session: Session,
    incomplete: bool = False,
) -> list[dict[str, Any]]:
    return get_model_version_axis_coverage(session, incomplete=incomplete)


def get_model_versions_response(
    session: Session,
    incomplete: bool = False,
) -> dict[str, Any]:
    rows = get_model_version_axis_coverage(session, incomplete=incomplete)
    return {
        "model_versions": rows,
        "total_model_versions": len(rows),
    }


def model_versions_endpoint(
    session: Session = Depends(get_session),
    incomplete: bool = False,
) -> dict[str, Any]:
    return get_model_versions_response(session, incomplete=incomplete)


if __name__ == "__main__":
    import importlib

    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    package_name = "services.staged.model_version_axis_coverage_api"
    try:
        service_package = importlib.import_module(package_name)
        target_router = service_package.router.router
    except (ImportError, AttributeError):
        target_router = None

    test_app = FastAPI()
    if target_router is not None:
        route_paths = [getattr(route, "path", "") for route in target_router.routes]
        mount_prefix = "" if any(path.startswith("/api/") for path in route_paths) else "/api"
        test_app.include_router(target_router, prefix=mount_prefix)
    else:
        @test_app.get("/api/scoring/model-versions")
        def model_versions(
            incomplete: bool = False,
            session: Session = Depends(get_session),
        ) -> dict[str, Any]:
            return get_model_versions_response(session, incomplete=incomplete)

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)
    McpLlmAxisScore.__table__.create(engine)

    def override_get_session():
        with SessionLocal() as session:
            yield session

    test_app.dependency_overrides[get_session] = override_get_session

    with SessionLocal() as session:
        rows: list[dict[str, Any]] = []
        for index, axis_name in enumerate(EXPECTED_AXES, start=1):
            rows.append(
                {
                    "id": index,
                    "server_id": "server-full",
                    "axis_name": axis_name,
                    "label": axis_name,
                    "label_index": index,
                    "probs": {},
                    "p_top": 0.1,
                    "p_critical": 0.1,
                    "p_danger": 0.1,
                    "escalated": axis_name == "exploit_surface",
                    "escalated_to": "security" if axis_name == "exploit_surface" else None,
                    "decision_rule_version": "v1",
                    "model_version": "model-full",
                    "adapter_sha256": "a" * 64,
                    "scored_at": datetime(2024, 1, 1, 0, index),
                }
            )

        for index, axis_name in enumerate(
            ("overall_risk", "auth_strength", "capability_breadth"),
            start=20,
        ):
            rows.append(
                {
                    "id": index,
                    "server_id": "server-partial",
                    "axis_name": axis_name,
                    "label": axis_name,
                    "label_index": index,
                    "probs": {},
                    "p_top": 0.2,
                    "p_critical": 0.2,
                    "p_danger": 0.2,
                    "escalated": False,
                    "escalated_to": None,
                    "decision_rule_version": "v1",
                    "model_version": "model-partial",
                    "adapter_sha256": "b" * 64,
                    "scored_at": datetime(2024, 1, 2, 0, index),
                }
            )

        session.add_all([McpLlmAxisScore(**row) for row in rows])
        session.commit()

    client = TestClient(test_app)
    response = client.get("/api/scoring/model-versions")
    assert response.status_code == 200
    payload = response.json()
    assert payload["total_model_versions"] == 2
    assert len(payload["model_versions"]) == 2

    versions = {row["model_version"]: row for row in payload["model_versions"]}
    assert versions["model-full"]["axis_coverage"] == 7
    assert versions["model-partial"]["axis_coverage"] == 3
    assert versions["model-full"]["servers"] == 1
    assert versions["model-partial"]["servers"] == 1
    assert versions["model-full"]["escalated_rows"] == 1
    assert versions["model-partial"]["escalated_rows"] == 0

    incomplete_response = client.get(
        "/api/scoring/model-versions",
        params={"incomplete": "true"},
    )
    assert incomplete_response.status_code == 200
    incomplete_payload = incomplete_response.json()
    assert incomplete_payload["total_model_versions"] == 1
    assert len(incomplete_payload["model_versions"]) == 1
    assert incomplete_payload["model_versions"][0]["model_version"] == "model-partial"

    print("PASS")