from __future__ import annotations

import importlib
import os
from collections.abc import Iterator
from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, Depends, FastAPI, Query
from fastapi.testclient import TestClient
from pydantic import BaseModel
from sqlalchemy import (
    CheckConstraint,
    Column,
    Float,
    Integer,
    MetaData,
    String,
    Table,
    bindparam,
    create_engine,
    text,
)
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool


AxisName = Literal[
    "overall_risk",
    "auth_strength",
    "capability_breadth",
    "data_sensitivity",
    "network_egress",
    "maintainer_trust",
    "exploit_surface",
]

VALID_AXIS_NAMES: tuple[AxisName, ...] = (
    "overall_risk",
    "auth_strength",
    "capability_breadth",
    "data_sensitivity",
    "network_egress",
    "maintainer_trust",
    "exploit_surface",
)


class LabelItem(BaseModel):
    label_index: int
    label: str
    count: int


class AxisLabels(BaseModel):
    axis_name: AxisName
    labels: list[LabelItem]


class LabelIndexResponse(BaseModel):
    generated_at: str
    axes: list[AxisLabels]
    total_axes: int


def _load_session_provider():
    candidates = (
        ("services.db.session", "get_session"),
        ("services.db.session", "get_db"),
        ("services.db.database", "get_session"),
        ("services.db.database", "get_db"),
        ("services.database.session", "get_session"),
        ("services.database.session", "get_db"),
        ("services.database", "get_session"),
        ("services.database", "get_db"),
    )
    for module_name, attribute_name in candidates:
        try:
            module = importlib.import_module(module_name)
        except ImportError:
            continue
        provider = getattr(module, attribute_name, None)
        if provider is not None:
            return provider
    return None


_real_session_provider = _load_session_provider()
_default_engine: Engine | None = None


def _fallback_session_provider() -> Iterator[Session]:
    global _default_engine
    if _default_engine is None:
        database_url = os.environ.get(
            "DATABASE_URL",
            "sqlite:///./axis_label_index_api.sqlite3",
        )
        _default_engine = create_engine(database_url)
    factory = sessionmaker(
        bind=_default_engine,
        class_=Session,
        expire_on_commit=False,
    )
    with factory() as session:
        yield session


if _real_session_provider is not None:
    get_session = _real_session_provider
else:
    get_session = _fallback_session_provider


router = APIRouter()


@router.get(
    "/axes/label-index",
    response_model=LabelIndexResponse,
)
def get_axis_label_index(
    axis: AxisName | None = Query(default=None),
    session: Session = Depends(get_session),
) -> LabelIndexResponse:
    axis_placeholders = ", ".join(f"'{name}'" for name in VALID_AXIS_NAMES)
    statement = text(
        f"""
        SELECT
            axis_name,
            label_index,
            MIN(label) AS label,
            COUNT(*) AS count
        FROM mcp_llm_axis_scores
        WHERE axis_name IN ({axis_placeholders})
          AND (:axis IS NULL OR axis_name = :axis)
        GROUP BY axis_name, label_index
        ORDER BY axis_name ASC, label_index ASC
        """
    ).bindparams(bindparam("axis", type_=String))

    rows = session.execute(statement, {"axis": axis}).mappings().all()

    axes: list[AxisLabels] = []
    for axis_name in VALID_AXIS_NAMES:
        axis_rows = [row for row in rows if row["axis_name"] == axis_name]
        if not axis_rows:
            continue
        labels = [
            LabelItem(
                label_index=int(row["label_index"]),
                label=str(row["label"]),
                count=int(row["count"]),
            )
            for row in axis_rows
        ]
        labels.sort(key=lambda item: item.label_index)
        axes.append(AxisLabels(axis_name=axis_name, labels=labels))

    return LabelIndexResponse(
        generated_at=datetime.now(timezone.utc).isoformat(),
        axes=axes,
        total_axes=len(axes),
    )


app = FastAPI()
app.include_router(router, prefix="/api")


def _build_test_engine() -> Engine:
    return create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )


def _build_test_metadata() -> MetaData:
    metadata = MetaData()
    Table(
        "mcp_llm_axis_scores",
        metadata,
        Column("axis_name", String(64), nullable=False),
        Column("label", String(255), nullable=False),
        Column("label_index", Integer, nullable=False),
        Column("p_top", Float, nullable=False),
        Column("p_critical", Float, nullable=False),
        Column("p_danger", Float, nullable=False),
        Column("server_id", String(255), nullable=False),
        CheckConstraint(
            "axis_name IN ("
            "'overall_risk', "
            "'auth_strength', "
            "'capability_breadth', "
            "'data_sensitivity', "
            "'network_egress', "
            "'maintainer_trust', "
            "'exploit_surface'"
            ")",
            name="ck_mcp_llm_axis_scores_axis_name",
        ),
    )
    return metadata


def run_contract() -> int:
    engine = _build_test_engine()
    metadata = _build_test_metadata()
    metadata.create_all(engine)

    factory = sessionmaker(
        bind=engine,
        class_=Session,
        expire_on_commit=False,
    )

    with factory() as session:
        session.execute(
            text(
                """
                INSERT INTO mcp_llm_axis_scores
                    (axis_name, label, label_index, p_top, p_critical, p_danger, server_id)
                VALUES
                    ('auth_strength', 'Weak', 0, 0.70, 0.20, 0.10, 'server-1'),
                    ('auth_strength', 'Strong', 1, 0.20, 0.10, 0.70, 'server-1'),
                    ('exploit_surface', 'Low', 0, 0.80, 0.10, 0.10, 'server-2')
                """
            )
        )
        session.commit()

    def override_get_session() -> Iterator[Session]:
        with factory() as session:
            yield session

    app.dependency_overrides[get_session] = override_get_session

    try:
        with TestClient(app) as client:
            response = client.get("/api/axes/label-index")
            assert response.status_code == 200, response.text

            payload = response.json()
            assert payload["total_axes"] == 2, payload
            assert set(payload) == {"generated_at", "axes", "total_axes"}, payload
            assert payload["generated_at"], payload

            returned_axes = payload["axes"]
            assert len(returned_axes) == 2, returned_axes
            assert all(
                axis["axis_name"] in VALID_AXIS_NAMES
                for axis in returned_axes
            ), returned_axes

            auth_strength = next(
                axis
                for axis in returned_axes
                if axis["axis_name"] == "auth_strength"
            )
            assert len(auth_strength["labels"]) == 2, auth_strength
            assert [
                item["label_index"] for item in auth_strength["labels"]
            ] == [0, 1], auth_strength
            assert all(
                isinstance(item["count"], int)
                for item in auth_strength["labels"]
            ), auth_strength

            exploit_surface = next(
                axis
                for axis in returned_axes
                if axis["axis_name"] == "exploit_surface"
            )
            assert len(exploit_surface["labels"]) == 1, exploit_surface
            assert exploit_surface["labels"][0]["label_index"] == 0, exploit_surface

            filtered_response = client.get(
                "/api/axes/label-index",
                params={"axis": "auth_strength"},
            )
            assert filtered_response.status_code == 200, filtered_response.text
            filtered_payload = filtered_response.json()
            assert filtered_payload["total_axes"] == 1, filtered_payload
            assert (
                filtered_payload["axes"][0]["axis_name"] == "auth_strength"
            ), filtered_payload
    finally:
        app.dependency_overrides.clear()
        engine.dispose()

    print("PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(run_contract())