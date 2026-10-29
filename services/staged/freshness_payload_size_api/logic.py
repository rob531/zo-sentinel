from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime
from typing import Any

from fastapi import Depends
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import FreshnessStat


def _decode_payload(payload: Any) -> Any:
    if isinstance(payload, bytes):
        payload = payload.decode("utf-8")
    if isinstance(payload, str):
        try:
            return json.loads(payload)
        except json.JSONDecodeError:
            return payload
    return payload


def _payload_size(payload: Any) -> tuple[int, int]:
    value = _decode_payload(payload)
    payload_bytes = len(json.dumps(value, ensure_ascii=False).encode("utf-8"))
    payload_key_count = len(value) if isinstance(value, dict) else 0
    return payload_bytes, payload_key_count


def _computed_at_key(value: Any) -> tuple[int, float | str]:
    if value is None:
        return (0, 0.0)
    if isinstance(value, datetime):
        return (1, value.timestamp())
    return (1, str(value))


def get_freshness_payload_sizes(db: Session = Depends(get_session)) -> dict[str, Any]:
    rows = (
        db.query(
            FreshnessStat.surface,
            FreshnessStat.payload,
            FreshnessStat.computed_at,
            FreshnessStat.compute_seconds,
        )
        .order_by(FreshnessStat.surface.asc(), FreshnessStat.computed_at.asc())
        .all()
    )

    grouped: dict[str, list[Any]] = defaultdict(list)
    for row in rows:
        grouped[row.surface].append(row)

    surfaces: list[dict[str, Any]] = []
    for surface, surface_rows in grouped.items():
        latest = max(
            surface_rows,
            key=lambda row: _computed_at_key(row.computed_at),
        )
        payload_bytes, payload_key_count = _payload_size(latest.payload)
        compute_values = [
            float(row.compute_seconds)
            for row in surface_rows
            if row.compute_seconds is not None
        ]

        surfaces.append(
            {
                "surface": surface,
                "payload_bytes": payload_bytes,
                "payload_key_count": payload_key_count,
                "avg_compute_seconds": (
                    sum(compute_values) / len(compute_values)
                    if compute_values
                    else None
                ),
                "max_compute_seconds": max(compute_values) if compute_values else None,
                "last_computed_at": latest.computed_at,
            }
        )

    largest_surface = (
        max(surfaces, key=lambda item: item["payload_bytes"])["surface"]
        if surfaces
        else None
    )
    total_payload_bytes = sum(item["payload_bytes"] for item in surfaces)

    return {
        "surfaces": surfaces,
        "summary": {
            "surface_count": len(surfaces),
            "largest_surface": largest_surface,
            "total_payload_bytes": total_payload_bytes,
        },
    }


get_payload_sizes = get_freshness_payload_sizes


if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    FreshnessStat.__table__.create(test_engine)
    TestSession = sessionmaker(bind=test_engine, autoflush=False, autocommit=False)

    seeded_payloads = {
        "surface_a": {"alpha": 1},
        "surface_b": {"beta": 2, "gamma": "value"},
        "surface_c": {"delta": True, "epsilon": None, "zeta": 3},
    }

    with TestSession() as session:
        session.add_all(
            [
                FreshnessStat(
                    surface=surface,
                    payload=payload,
                    computed_at=datetime(2024, 1, 1, 0, 0, index),
                    compute_seconds=0.25 * (index + 1),
                )
                for index, (surface, payload) in enumerate(seeded_payloads.items())
            ]
        )
        session.commit()

    that_app = FastAPI()
    that_app.dependency_overrides[get_session] = lambda: TestSession()

    @that_app.get("/api/freshness/payload-sizes")
    def freshness_payload_sizes(db: Session = Depends(get_session)) -> dict[str, Any]:
        return get_freshness_payload_sizes(db)

    response = TestClient(that_app).get("/api/freshness/payload-sizes")
    assert response.status_code == 200
    body = response.json()
    assert len(body["surfaces"]) == 3
    assert body["summary"]["surface_count"] == 3
    assert body["summary"]["total_payload_bytes"] == sum(
        len(json.dumps(payload, ensure_ascii=False).encode("utf-8"))
        for payload in seeded_payloads.values()
    )
    for surface in body["surfaces"]:
        assert surface["payload_key_count"] == len(seeded_payloads[surface["surface"]])
    assert body["summary"]["largest_surface"] == max(
        seeded_payloads,
        key=lambda surface: len(
            json.dumps(seeded_payloads[surface], ensure_ascii=False).encode("utf-8")
        ),
    )
    print("PASS")