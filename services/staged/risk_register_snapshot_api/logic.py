from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any, Dict, Optional

from fastapi import Depends, FastAPI
from sqlalchemy import MetaData, Table, asc, desc, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry


def _iso_timestamp(value: Any) -> str:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, date):
        parsed = datetime.combine(value, datetime.min.time())
    elif isinstance(value, (int, float)):
        parsed = datetime.fromtimestamp(value, tz=timezone.utc)
    elif isinstance(value, str):
        normalized = value.strip()
        if normalized.endswith("Z"):
            normalized = normalized[:-1] + "+00:00"
        parsed = datetime.fromisoformat(normalized)
    else:
        raise ValueError(f"Unsupported timestamp value: {value!r}")

    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _first_value(row: Any, names: tuple[str, ...]) -> Any:
    mapping = row._mapping
    for name in names:
        if name in mapping and mapping[name] is not None:
            return mapping[name]
    return None


def _float_or_none(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def get_risk_register_snapshots(
    server_id: Optional[str] = None,
    limit: int = 50,
    session: Session = Depends(get_session),
) -> Dict[str, Any]:
    bind = session.get_bind()
    risk_register = Table(
        "mcp_risk_register",
        MetaData(),
        autoload_with=bind,
    )
    registry = McpServerRegistry.__table__

    joined_columns = (
        risk_register,
        registry.c.name.label("_registry_name"),
        registry.c.risk_tier.label("_current_risk_tier"),
    )

    if server_id is not None:
        statement = (
            select(*joined_columns)
            .select_from(
                risk_register.join(
                    registry,
                    registry.c.server_id == risk_register.c.server_id,
                )
            )
            .where(risk_register.c.server_id == server_id)
            .order_by(
                asc(risk_register.c.computed_at.is_(None)),
                asc(risk_register.c.computed_at),
                asc(risk_register.c.id),
            )
        )
    else:
        normalized_limit = max(1, int(limit))
        recent = (
            select(*joined_columns)
            .select_from(
                risk_register.join(
                    registry,
                    registry.c.server_id == risk_register.c.server_id,
                )
            )
            .order_by(
                asc(risk_register.c.computed_at.is_(None)),
                desc(risk_register.c.computed_at),
                desc(risk_register.c.id),
            )
            .limit(normalized_limit)
            .subquery()
        )
        statement = select(recent).order_by(
            asc(recent.c.computed_at.is_(None)),
            asc(recent.c.computed_at),
            asc(recent.c.id),
        )

    rows = session.execute(statement).all()
    snapshots = []

    for row in rows:
        historical_tier = _first_value(row, ("risk_tier", "tier"))
        current_tier = row._mapping.get("_current_risk_tier")
        composite_score = _first_value(
            row,
            (
                "trust_score",
                "composite_score",
                "composite_risk_score",
                "risk_score",
            ),
        )
        verdict = _first_value(row, ("verdict", "risk_verdict"))
        name = row._mapping.get("_registry_name")

        snapshots.append(
            {
                "server_id": str(row._mapping["server_id"]),
                "name": str(name) if name is not None else "",
                "computed_at": _iso_timestamp(row._mapping["computed_at"]),
                "risk_tier": str(
                    historical_tier
                    if historical_tier is not None
                    else current_tier
                    if current_tier is not None
                    else ""
                ),
                "verdict": str(verdict) if verdict is not None else "",
                "trust_score": _float_or_none(composite_score),
            }
        )

    return {
        "server_id": str(server_id) if server_id is not None else None,
        "snapshots": snapshots,
        "count": len(snapshots),
        "as_of": _iso_timestamp(datetime.now(timezone.utc)),
    }


if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestingSessionLocal = sessionmaker(
        bind=engine,
        autocommit=False,
        autoflush=False,
    )

    McpServerRegistry.__table__.create(engine)
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE mcp_risk_register (
                    id INTEGER PRIMARY KEY,
                    server_id TEXT NOT NULL,
                    computed_at TEXT NOT NULL,
                    risk_tier TEXT NOT NULL,
                    verdict TEXT NOT NULL,
                    trust_score REAL
                )
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO mcp_risk_register
                    (id, server_id, computed_at, risk_tier, verdict, trust_score)
                VALUES
                    (1, 'srv-alpha', '2025-01-02T12:00:00Z', 'medium', 'review', 71.5),
                    (2, 'srv-beta', '2025-01-01T12:00:00Z', 'low', 'allow', 92.0),
                    (3, 'srv-alpha', '2025-01-03T12:00:00Z', 'low', 'allow', 88.25)
                """
            )
        )

    with TestingSessionLocal() as db_session:
        db_session.add_all(
            [
                McpServerRegistry(
                    server_id="srv-alpha",
                    name="Alpha Server",
                    risk_tier="low",
                    trust_score=88.25,
                ),
                McpServerRegistry(
                    server_id="srv-beta",
                    name="Beta Server",
                    risk_tier="low",
                    trust_score=92.0,
                ),
            ]
        )
        db_session.commit()

    test_app = FastAPI()

    @test_app.get("/api/risk/register/snapshots")
    def risk_register_snapshots(
        server_id: Optional[str] = None,
        limit: int = 50,
        db_session: Session = Depends(get_session),
    ) -> Dict[str, Any]:
        return get_risk_register_snapshots(
            server_id=server_id,
            limit=limit,
            session=db_session,
        )

    def override_get_session():
        with TestingSessionLocal() as db_session:
            yield db_session

    test_app.dependency_overrides[get_session] = override_get_session

    response = TestClient(test_app).get("/api/risk/register/snapshots")
    payload = response.json()

    assert response.status_code == 200
    assert payload["count"] == 3
    assert len(payload["snapshots"]) == 3
    assert payload["server_id"] is None
    assert all(snapshot["name"] for snapshot in payload["snapshots"])
    assert payload["snapshots"] == sorted(
        payload["snapshots"],
        key=lambda snapshot: snapshot["computed_at"],
    )
    datetime.fromisoformat(payload["as_of"].replace("Z", "+00:00"))

    print("PASS")