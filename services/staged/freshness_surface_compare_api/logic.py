from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import FreshnessStat


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _age_seconds(computed_at: datetime, now: datetime) -> int:
    age = (now - _as_utc(computed_at)).total_seconds()
    return max(0, int(age))


def compare_freshness(session: Session, surface: str | None = None) -> dict[str, Any]:
    statement = select(
        FreshnessStat.surface,
        FreshnessStat.computed_at,
        FreshnessStat.compute_seconds,
    )
    if surface is not None:
        statement = statement.where(FreshnessStat.surface == surface)

    rows = session.execute(statement).all()
    now = datetime.now(timezone.utc)
    newest: dict[str, tuple[datetime, Any]] = {}

    for row in rows:
        row_surface = row.surface
        computed_at = row.computed_at
        if row_surface not in newest or computed_at > newest[row_surface][0]:
            newest[row_surface] = (computed_at, row.compute_seconds)

    surfaces: list[dict[str, Any]] = []
    stale: list[dict[str, Any]] = []

    for row_surface, (computed_at, compute_seconds) in newest.items():
        age_seconds = _age_seconds(computed_at, now)
        if age_seconds < 300:
            status = "fresh"
        elif age_seconds < 3600:
            status = "aging"
        else:
            status = "stale"

        surface_result = {
            "surface": row_surface,
            "computed_at": computed_at,
            "age_seconds": age_seconds,
            "compute_seconds": compute_seconds,
            "status": status,
        }
        surfaces.append(surface_result)

        if status == "stale":
            stale.append(
                {
                    "surface": row_surface,
                    "age_seconds": age_seconds,
                }
            )

    surfaces.sort(key=lambda item: item["surface"])
    stale.sort(key=lambda item: item["surface"])
    return {"surfaces": surfaces, "stale": stale}


def get_freshness_comparison(
    session: Session, surface: str | None = None
) -> dict[str, Any]:
    return compare_freshness(session, surface)


def freshness_compare(
    session: Session, surface: str | None = None
) -> dict[str, Any]:
    return compare_freshness(session, surface)


def compare_surfaces(
    session: Session, surface: str | None = None
) -> dict[str, Any]:
    return compare_freshness(session, surface)


__all__ = [
    "compare_freshness",
    "compare_surfaces",
    "freshness_compare",
    "get_freshness_comparison",
    "get_session",
]


if __name__ == "__main__":
    from datetime import timedelta

    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    engine = create_engine("sqlite://")
    FreshnessStat.__table__.create(engine)
    testing_session = sessionmaker(bind=engine)()

    base_time = datetime.now(timezone.utc).replace(tzinfo=None)
    testing_session.add_all(
        [
            FreshnessStat(
                surface="alpha",
                payload={},
                computed_at=base_time - timedelta(seconds=60),
                compute_seconds=1.5,
            ),
            FreshnessStat(
                surface="beta",
                payload={},
                computed_at=base_time - timedelta(seconds=600),
                compute_seconds=2.5,
            ),
            FreshnessStat(
                surface="gamma",
                payload={},
                computed_at=base_time - timedelta(seconds=7200),
                compute_seconds=3.5,
            ),
        ]
    )
    testing_session.commit()

    result = compare_freshness(testing_session)
    assert len(result["surfaces"]) == 3
    assert len(result["stale"]) == 1
    assert result["stale"][0]["surface"] == "gamma"
    print("PASS")