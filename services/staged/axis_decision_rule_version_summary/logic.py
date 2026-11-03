"""Decision-rule-version aggregation over the real application score store."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List

from sqlalchemy import distinct, func, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore


def _configured_axis_names() -> tuple[str, ...]:
    """Discover the persisted enum values without coupling to a duplicate enum."""
    column_type = McpLlmAxisScore.__table__.c.axis_name.type
    enum_values = getattr(column_type, "enums", None)
    if enum_values:
        return tuple(str(value) for value in enum_values)

    enum_class = getattr(column_type, "enum_class", None)
    if enum_class is not None and issubclass(enum_class, Enum):
        return tuple(str(member.value) for member in enum_class)

    for constraint in McpLlmAxisScore.__table__.constraints:
        expression = str(getattr(constraint, "sqltext", ""))
        if "axis_name" not in expression.lower():
            continue
        values = tuple(re.findall(r"'([^']+)'", expression))
        if values:
            return values

    return ()


AXIS_NAMES = _configured_axis_names()


def get_decision_rule_version_summary(db: Session) -> List[Dict[str, Any]]:
    """Summarize scores by decision-rule version and model version."""
    overall_rows = db.execute(
        select(
            McpLlmAxisScore.decision_rule_version,
            McpLlmAxisScore.model_version,
            func.count(McpLlmAxisScore.id),
            func.count(distinct(McpLlmAxisScore.server_id)),
            func.min(McpLlmAxisScore.scored_at),
            func.max(McpLlmAxisScore.scored_at),
        )
        .group_by(
            McpLlmAxisScore.decision_rule_version,
            McpLlmAxisScore.model_version,
        )
    ).all()

    axis_rows = db.execute(
        select(
            McpLlmAxisScore.decision_rule_version,
            McpLlmAxisScore.model_version,
            McpLlmAxisScore.axis_name,
            func.count(McpLlmAxisScore.id),
        )
        .group_by(
            McpLlmAxisScore.decision_rule_version,
            McpLlmAxisScore.model_version,
            McpLlmAxisScore.axis_name,
        )
    ).all()

    versions: Dict[tuple[str, str], Dict[str, Any]] = {}

    for decision_version, model_version, scored_rows, servers, first_at, last_at in overall_rows:
        key = (
            str(decision_version) if decision_version is not None else "unknown",
            str(model_version) if model_version is not None else "unknown",
        )
        versions[key] = {
            "decision_rule_version": key[0],
            "model_version": key[1],
            "scored_rows": int(scored_rows or 0),
            "servers": int(servers or 0),
            "first_scored_at": first_at,
            "last_scored_at": last_at,
            "axes": {axis_name: 0 for axis_name in AXIS_NAMES},
        }

    for decision_version, model_version, axis_name, count in axis_rows:
        key = (
            str(decision_version) if decision_version is not None else "unknown",
            str(model_version) if model_version is not None else "unknown",
        )
        if key not in versions:
            versions[key] = {
                "decision_rule_version": key[0],
                "model_version": key[1],
                "scored_rows": 0,
                "servers": 0,
                "first_scored_at": None,
                "last_scored_at": None,
                "axes": {axis_name: 0 for axis_name in AXIS_NAMES},
            }
        versions[key]["axes"][str(axis_name)] = int(count or 0)

    return sorted(
        versions.values(),
        key=lambda row: (
            row["decision_rule_version"],
            row["model_version"],
        ),
    )


get_decision_rule_versions = get_decision_rule_version_summary
get_axis_decision_rule_versions = get_decision_rule_version_summary
summarize_decision_rule_versions = get_decision_rule_version_summary
decision_rule_version_summary = get_decision_rule_version_summary
build_decision_rule_version_summary = get_decision_rule_version_summary
get_version_summary = get_decision_rule_version_summary


def _self_test() -> None:
    from fastapi import Depends, FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    McpLlmAxisScore.__table__.create(engine)
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)

    axis_names = AXIS_NAMES
    if len(axis_names) < 2:
        axis_names = ("axis_one", "axis_two")
    axis_one, axis_two = axis_names[:2]

    now = datetime.now(timezone.utc)
    rows = [
        McpLlmAxisScore(
            id=1,
            axis_name=axis_one,
            decision_rule_version="rules-v1",
            model_version="model-a",
            server_id="server-a",
            scored_at=now,
        ),
        McpLlmAxisScore(
            id=2,
            axis_name=axis_one,
            decision_rule_version="rules-v1",
            model_version="model-a",
            server_id="server-b",
            scored_at=now,
        ),
        McpLlmAxisScore(
            id=3,
            axis_name=axis_two,
            decision_rule_version="rules-v1",
            model_version="model-a",
            server_id="server-a",
            scored_at=now,
        ),
        McpLlmAxisScore(
            id=4,
            axis_name=axis_one,
            decision_rule_version="rules-v2",
            model_version="model-a",
            server_id="server-c",
            scored_at=now,
        ),
        McpLlmAxisScore(
            id=5,
            axis_name=axis_two,
            decision_rule_version="rules-v2",
            model_version="model-a",
            server_id="server-d",
            scored_at=now,
        ),
    ]

    with session_factory() as session:
        session.add_all(rows)
        session.commit()

    test_app = FastAPI()

    def override_session():
        with session_factory() as session:
            yield session

    test_app.dependency_overrides[get_session] = override_session

    @test_app.get("/api/axis/decision-rule-versions")
    def decision_rule_versions(db: Session = Depends(get_session)):
        return {"versions": get_decision_rule_version_summary(db)}

    with TestClient(test_app) as client:
        response = client.get("/api/axis/decision-rule-versions")
        assert response.status_code == 200
        payload = response.json()
        assert len(payload["versions"]) == 2

        by_version = {
            row["decision_rule_version"]: row for row in payload["versions"]
        }
        assert by_version["rules-v1"]["axes"][axis_one] == 2
        assert by_version["rules-v1"]["axes"][axis_two] == 1
        assert by_version["rules-v2"]["axes"][axis_one] == 1
        assert by_version["rules-v2"]["axes"][axis_two] == 1

    print("PASS")


if __name__ == "__main__":
    _self_test()