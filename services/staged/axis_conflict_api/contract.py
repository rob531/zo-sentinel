"""Acceptance self-test for the axis conflict API."""
from __future__ import annotations

import ast
import inspect
import sys
from collections.abc import Collection


def _legal_axis_names() -> tuple[str, ...]:
    from . import logic, router

    modules = (logic, router)
    for module in modules:
        for value in vars(module).values():
            if (
                isinstance(value, Collection)
                and not isinstance(value, (str, bytes, bytearray))
                and len(value) == 7
                and all(isinstance(item, str) for item in value)
                and len(set(value)) == 7
            ):
                return tuple(value)

    for module in modules:
        try:
            tree = ast.parse(inspect.getsource(module))
        except (OSError, TypeError):
            continue
        for node in ast.walk(tree):
            if not isinstance(node, (ast.Assign, ast.AnnAssign)):
                continue
            value = node.value
            if isinstance(value, (ast.Tuple, ast.List, ast.Set)):
                try:
                    names = tuple(ast.literal_eval(value))
                except (ValueError, TypeError):
                    continue
                if (
                    len(names) == 7
                    and all(isinstance(item, str) for item in names)
                    and len(set(names)) == 7
                ):
                    return names

    raise AssertionError("the seven legal axis names are not exposed by the service")


def run() -> bool:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.db import get_session
    from app.models import Base, McpLlmAxisScore, McpServerRegistry

    from .router import router

    axis_names = _legal_axis_names()
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
                McpServerRegistry(
                    server_id="axis-contract-low",
                    name="Low tier with escalated axis",
                    risk_tier="LOW",
                    verdict="allow",
                ),
                McpServerRegistry(
                    server_id="axis-contract-high",
                    name="High tier with critical axis",
                    risk_tier="HIGH",
                    verdict="block",
                ),
                McpServerRegistry(
                    server_id="axis-contract-match",
                    name="Matching tier with complete axes",
                    risk_tier="MEDIUM",
                    verdict="review",
                ),
            ]
        )

        score_id = 1
        session.add(
            McpLlmAxisScore(
                id=score_id,
                server_id="axis-contract-low",
                axis_name=axis_names[0],
                model_version="contract-test-v1",
                p_top=0.90,
                p_critical=0.10,
                p_danger=0.20,
                escalated=True,
                escalated_to="HIGH",
                label="escalated",
            )
        )
        score_id += 1

        session.add(
            McpLlmAxisScore(
                id=score_id,
                server_id="axis-contract-high",
                axis_name=axis_names[1],
                model_version="contract-test-v1",
                p_top=0.80,
                p_critical=0.95,
                p_danger=0.40,
                escalated=False,
                label="critical",
            )
        )
        score_id += 1

        for axis_name in axis_names:
            session.add(
                McpLlmAxisScore(
                    id=score_id,
                    server_id="axis-contract-match",
                    axis_name=axis_name,
                    model_version="contract-test-v1",
                    p_top=0.05,
                    p_critical=0.01,
                    p_danger=0.02,
                    escalated=False,
                    label="matching",
                )
            )
            score_id += 1

        session.commit()
    finally:
        session.close()

    def override_session():
        db = testing_session()
        try:
            yield db
        finally:
            db.close()

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = override_session

    with TestClient(app) as client:
        response = client.get("/api/axis-conflicts", params={"days": 30})
        assert response.status_code == 200, response.text
        body = response.json()
        assert "conflicts" in body, body
        assert "total" in body["counts"], body
        assert any(
            conflict.get("conflict_kind") == "escalated_axis_vs_low_tier"
            for conflict in body["conflicts"]
        ), body

    return True


if __name__ == "__main__":
    try:
        run()
    except Exception as exc:  # noqa: BLE001
        print(f"FAIL: {exc!r}")
        sys.exit(1)
    print("PASS")
    sys.exit(0)