from datetime import datetime, timezone
import json
from typing import Any, Dict, List

from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import FreshnessStat


class SurfacePayloadAudit(BaseModel):
    surface: str
    computed_at: str
    age_seconds: float
    stale: bool
    compute_seconds: float
    key_count: int
    keys: List[str]


class PayloadAuditResponse(BaseModel):
    surfaces: List[SurfacePayloadAudit]
    total_surfaces: int


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _payload_keys(payload: Any) -> List[str]:
    if isinstance(payload, dict):
        return sorted(str(key) for key in payload.keys())
    return []


def get_payload_audit(session: Session = Depends(get_session)) -> Dict[str, Any]:
    rows = session.execute(
        select(
            FreshnessStat.surface,
            FreshnessStat.payload,
            FreshnessStat.computed_at,
            FreshnessStat.compute_seconds,
        )
    ).all()

    now = datetime.now(timezone.utc)
    surfaces: List[Dict[str, Any]] = []

    for surface, payload, computed_at, compute_seconds in rows:
        computed_at_utc = _as_utc(computed_at)
        age_seconds = float((now - computed_at_utc).total_seconds())
        keys = _payload_keys(payload)

        surfaces.append(
            {
                "surface": str(surface),
                "computed_at": computed_at_utc.isoformat(),
                "age_seconds": age_seconds,
                "stale": age_seconds > 3600,
                "compute_seconds": float(compute_seconds or 0.0),
                "key_count": len(keys),
                "keys": keys,
            }
        )

    return {
        "surfaces": surfaces,
        "total_surfaces": len(surfaces),
    }


def create_app() -> FastAPI:
    application = FastAPI()
    application.get(
        "/api/freshness/surfaces/payload-audit",
        response_model=PayloadAuditResponse,
    )(get_payload_audit)
    return application


app = create_app()


def _run_self_test() -> None:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestingSessionLocal = sessionmaker(
        autocommit=False,
        autoflush=False,
        bind=engine,
        expire_on_commit=False,
    )

    with engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE freshness_stats (
                    surface VARCHAR,
                    payload JSON,
                    computed_at DATETIME,
                    compute_seconds FLOAT
                )
                """
            )
        )

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    with TestingSessionLocal() as session:
        session.execute(
            text(
                """
                INSERT INTO freshness_stats
                    (surface, payload, computed_at, compute_seconds)
                VALUES
                    (:surface, :payload, :computed_at, :compute_seconds)
                """
            ),
            [
                {
                    "surface": "fresh",
                    "payload": json.dumps({"b": 2, "a": 1}),
                    "computed_at": now,
                    "compute_seconds": 0.25,
                },
                {
                    "surface": "stale",
                    "payload": json.dumps({"old": True}),
                    "computed_at": now.replace(year=now.year - 1),
                    "compute_seconds": 1.5,
                },
            ],
        )
        session.commit()

    def override_get_session():
        with TestingSessionLocal() as session:
            yield session

    that_app = create_app()
    that_app.dependency_overrides[get_session] = override_get_session

    with TestClient(that_app) as client:
        response = client.get("/api/freshness/surfaces/payload-audit")
        assert response.status_code == 200
        body = response.json()
        assert body["total_surfaces"] == 2

        by_surface = {item["surface"]: item for item in body["surfaces"]}
        assert by_surface["fresh"]["key_count"] == 2
        assert by_surface["fresh"]["keys"] == ["a", "b"]
        assert by_surface["fresh"]["stale"] is False
        assert by_surface["stale"]["stale"] is True

    print("PASS")


if __name__ == "__main__":
    _run_self_test()