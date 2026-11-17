"""Logic for the freshness_surface_inventory service."""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from typing import Any, Dict, List

from fastapi import Depends, FastAPI
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import FreshnessStat


FRESH_MAX_AGE_SECONDS = 3600
STALE_MAX_AGE_SECONDS = 86400


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _parse_payload(payload: Any) -> Any:
    if isinstance(payload, (bytes, bytearray)):
        payload = payload.decode("utf-8")
    if isinstance(payload, str):
        return json.loads(payload)
    return payload


def get_freshness_surfaces(db: Session) -> Dict[str, Any]:
    """Return the freshness posture of every recorded data surface."""
    now = _utc_now()
    rows = db.execute(
        select(FreshnessStat).order_by(FreshnessStat.surface)
    ).scalars().all()

    surfaces: List[Dict[str, Any]] = []
    for row in rows:
        _parse_payload(row.payload)
        computed_at = _as_utc(row.computed_at)
        age_seconds = (now - computed_at).total_seconds()
        if age_seconds <= FRESH_MAX_AGE_SECONDS:
            status = "fresh"
        elif age_seconds <= STALE_MAX_AGE_SECONDS:
            status = "stale"
        else:
            status = "expired"

        surfaces.append(
            {
                "surface": row.surface,
                "computed_at": row.computed_at,
                "compute_seconds": float(row.compute_seconds),
                "age_seconds": age_seconds,
                "status": status,
            }
        )

    fresh = sum(1 for surface in surfaces if surface["status"] == "fresh")
    stale = sum(1 for surface in surfaces if surface["status"] == "stale")
    expired = sum(1 for surface in surfaces if surface["status"] == "expired")
    oldest_age_seconds = max(
        (surface["age_seconds"] for surface in surfaces), default=0.0
    )

    return {
        "surfaces": surfaces,
        "summary": {
            "total": len(surfaces),
            "fresh": fresh,
            "stale": stale,
            "expired": expired,
            "oldest_age_seconds": oldest_age_seconds,
        },
    }


def list_freshness_surfaces(db: Session) -> Dict[str, Any]:
    """Compatibility alias for consumers that use the list-style name."""
    return get_freshness_surfaces(db)


async def _read_http_body(receive):
    while True:
        message = await receive()
        if message["type"] == "http.request":
            body = message.get("body", b"")
            if not message.get("more_body", False):
                return body
        elif message["type"] == "http.disconnect":
            return b""


async def _request_status(app: FastAPI, path: str) -> int:
    status_code = 500
    body = b""

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        nonlocal status_code, body
        if message["type"] == "http.response.start":
            status_code = message["status"]
        elif message["type"] == "http.response.body":
            body += message.get("body", b"")

    scope = {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": path,
        "raw_path": path.encode("ascii"),
        "query_string": b"",
        "root_path": "",
        "headers": [(b"host", b"testserver")],
        "client": ("127.0.0.1", 12345),
        "server": ("testserver", 80),
    }
    await app(scope, receive, send)
    return status_code


def _self_test() -> None:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    FreshnessStat.__table__.create(engine)
    SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)

    now = _utc_now()
    with SessionLocal() as session:
        session.add_all(
            [
                FreshnessStat(
                    surface="alpha",
                    payload=json.dumps({"source": "alpha"}),
                    computed_at=now.replace(tzinfo=None),
                    compute_seconds=0.1,
                ),
                FreshnessStat(
                    surface="beta",
                    payload=json.dumps({"source": "beta"}),
                    computed_at=(now.replace(tzinfo=None) - __import__("datetime").timedelta(hours=2)),
                    compute_seconds=0.2,
                ),
                FreshnessStat(
                    surface="gamma",
                    payload=json.dumps({"source": "gamma"}),
                    computed_at=(now.replace(tzinfo=None) - __import__("datetime").timedelta(days=3)),
                    compute_seconds=0.3,
                ),
            ]
        )
        session.commit()

    def override_get_session():
        with SessionLocal() as session:
            yield session

    that_app = FastAPI()
    that_app.dependency_overrides[get_session] = override_get_session

    @that_app.get("/freshness/surfaces")
    def freshness_surfaces(session: Session = Depends(get_session)):
        return get_freshness_surfaces(session)

    status_code = asyncio.run(_request_status(that_app, "/freshness/surfaces"))
    if status_code != 200:
        raise RuntimeError(f"expected HTTP 200, got {status_code}")

    with SessionLocal() as session:
        result = get_freshness_surfaces(session)
        summary = result["summary"]
        if len(result["surfaces"]) != 3:
            raise AssertionError("expected three freshness surfaces")
        if (
            summary["fresh"],
            summary["stale"],
            summary["expired"],
        ) != (1, 1, 1):
            raise AssertionError(f"unexpected summary: {summary}")
        expired = next(
            surface for surface in result["surfaces"] if surface["surface"] == "gamma"
        )
        if expired["status"] != "expired":
            raise AssertionError("expected gamma to be expired")

    print("PASS")


if __name__ == "__main__":
    _self_test()