from __future__ import annotations

import ast
import inspect
import uuid
from datetime import datetime, timezone

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry


try:
    from services.staged.server_tier_compare.router import router
except ImportError:
    from services.staged.server_tier_compare.router import api_router as router


_FALLBACK_AXES = (
    "prompt_injection",
    "tool_poisoning",
    "data_exfiltration",
    "credential_theft",
    "command_injection",
    "privilege_escalation",
    "resource_abuse",
)


def _axis_order_from_module(module) -> tuple[str, ...] | None:
    for name in ("AXIS_ORDER", "AXIS_NAMES", "AXES", "AXIS_SEQUENCE"):
        value = getattr(module, name, None)
        if isinstance(value, (list, tuple)) and len(value) == 7:
            if all(isinstance(item, str) and item for item in value):
                return tuple(value)

    try:
        source = inspect.getsource(module)
    except (OSError, TypeError):
        return None

    try:
        tree = ast.parse(source)
    except SyntaxError:
        return None

    candidates = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        value = node.value
        if not isinstance(value, (ast.List, ast.Tuple)):
            continue
        if len(value.elts) != 7:
            continue
        if not all(isinstance(element, ast.Constant) and isinstance(element.value, str)
                   for element in value.elts):
            continue
        target_names = []
        for target in targets:
            if isinstance(target, ast.Name):
                target_names.append(target.id)
        if any("axis" in name.lower() for name in target_names):
            candidates.append(tuple(element.value for element in value.elts))

    return candidates[0] if candidates else None


def _get_axis_order() -> tuple[str, ...]:
    try:
        from services.staged.server_tier_compare import logic
    except ImportError:
        logic = None

    for module in (logic, router):
        if module is not None:
            axis_order = _axis_order_from_module(module)
            if axis_order is not None:
                return axis_order

    return _FALLBACK_AXES


def _make_app(session) -> FastAPI:
    application = FastAPI()

    route_paths = [getattr(route, "path", "") for route in router.routes]
    if any(path.startswith("/api/") for path in route_paths):
        application.include_router(router)
    else:
        application.include_router(router, prefix="/api")

    def override_get_session():
        yield session

    application.dependency_overrides[get_session] = override_get_session
    return application


def run_self_test() -> None:
    axis_order = _get_axis_order()
    assert len(axis_order) == 7
    assert len(set(axis_order)) == 7

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    McpServerRegistry.__table__.create(engine)
    McpLlmAxisScore.__table__.create(engine)

    server_one = f"server-contract-{uuid.uuid4().hex[:12]}"
    server_two = f"server-contract-{uuid.uuid4().hex[:12]}"
    scored_at = datetime.now(timezone.utc)

    server_one_values = (0.91, 0.12, 0.20, 0.08, 0.15, 0.05, 0.10)
    server_two_values = (0.20, 0.30, 0.55, 0.25, 0.10, 0.15, 0.18)

    with SessionLocal() as session:
        session.add_all(
            [
                McpServerRegistry(
                    server_id=server_one,
                    name="Contract Server One",
                    risk_tier="high",
                    trust_score=0.35,
                ),
                McpServerRegistry(
                    server_id=server_two,
                    name="Contract Server Two",
                    risk_tier="low",
                    trust_score=0.85,
                ),
            ]
        )

        for index, axis_name in enumerate(axis_order):
            session.add(
                McpLlmAxisScore(
                    id=str(uuid.uuid4()),
                    server_id=server_one,
                    axis_name=axis_name,
                    label=f"server-one-{axis_name}",
                    p_top=server_one_values[index],
                    p_critical=0.01,
                    p_danger=0.02,
                    escalated=False,
                    escalated_to=None,
                    model_version="contract-test-model",
                    scored_at=scored_at,
                )
            )
            session.add(
                McpLlmAxisScore(
                    id=str(uuid.uuid4()),
                    server_id=server_two,
                    axis_name=axis_name,
                    label=f"server-two-{axis_name}",
                    p_top=server_two_values[index],
                    p_critical=0.03,
                    p_danger=0.04,
                    escalated=False,
                    escalated_to=None,
                    model_version="contract-test-model",
                    scored_at=scored_at,
                )
            )

        session.commit()

        application = _make_app(session)
        with TestClient(application) as client:
            response = client.get(
                "/api/compare/tiers",
                params={"server_ids": f"{server_one},{server_two}"},
            )

        assert response.status_code == 200, response.text
        payload = response.json()
        assert "servers" in payload
        assert "axis_order" in payload
        assert "riskiest_axis_per_server" in payload

        returned_servers = {item["server_id"]: item for item in payload["servers"]}
        assert server_one in returned_servers
        assert server_two in returned_servers

        expected_maxima = {
            server_one: axis_order[max(range(7), key=lambda i: server_one_values[i])],
            server_two: axis_order[max(range(7), key=lambda i: server_two_values[i])],
        }
        expected_values = {
            server_one: max(server_one_values),
            server_two: max(server_two_values),
        }

        for server_id in (server_one, server_two):
            item = returned_servers[server_id]
            assert len(item["axes"]) == 7
            assert payload["riskiest_axis_per_server"][server_id] == expected_maxima[server_id]
            assert float(item["axes"][expected_maxima[server_id]]["p_top"]) == expected_values[server_id]

    engine.dispose()
    print("PASS")


def main() -> None:
    run_self_test()


if __name__ == "__main__":
    main()