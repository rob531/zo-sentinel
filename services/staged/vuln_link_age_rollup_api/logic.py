from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import Depends
from sqlalchemy import Integer, case, func, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import VulnAdvisory, VulnLink


_AGE_BUCKETS = ("0-7", "8-30", "31-90", "90+")


def _age_days_expression(column: Any, dialect_name: str) -> Any:
    if dialect_name == "postgresql":
        return func.floor(
            func.extract("epoch", func.age(func.current_timestamp(), column)) / 86400.0
        )
    return func.cast(
        func.julianday(func.current_timestamp()) - func.julianday(column),
        Integer,
    )


def get_link_age_rollup(
    db: Session = Depends(get_session),
    days: int = 365,
) -> dict[str, Any]:
    if days < 1:
        raise ValueError("days must be at least 1")

    dialect_name = db.get_bind().dialect.name
    cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=days)

    linked_age_days = _age_days_expression(VulnLink.linked_at, dialect_name)
    published_age_days = _age_days_expression(
        VulnAdvisory.published_at, dialect_name
    )

    base = (
        select(VulnLink.id)
        .join(VulnAdvisory, VulnAdvisory.id == VulnLink.advisory_id)
        .where(VulnLink.linked_at >= cutoff)
    )

    total_links = int(db.execute(select(func.count()).select_from(base.subquery())).scalar_one())

    bucket_expression = case(
        (linked_age_days <= 7, "0-7"),
        (linked_age_days <= 30, "8-30"),
        (linked_age_days <= 90, "31-90"),
        else_="90+",
    )

    bucket_rows = db.execute(
        select(bucket_expression, func.count(VulnLink.id))
        .join(VulnAdvisory, VulnAdvisory.id == VulnLink.advisory_id)
        .where(VulnLink.linked_at >= cutoff)
        .group_by(bucket_expression)
    ).all()

    age_buckets = {bucket: 0 for bucket in _AGE_BUCKETS}
    for bucket, count in bucket_rows:
        age_buckets[bucket] = int(count)

    severity_rows = db.execute(
        select(func.coalesce(VulnAdvisory.severity, "unknown"), func.count(VulnLink.id))
        .join(VulnAdvisory, VulnAdvisory.id == VulnLink.advisory_id)
        .where(VulnLink.linked_at >= cutoff)
        .group_by(VulnAdvisory.severity)
    ).all()

    by_severity = {str(severity): int(count) for severity, count in severity_rows}

    match_basis_rows = db.execute(
        select(
            func.coalesce(VulnLink.match_basis, "unknown"),
            func.count(VulnLink.id),
        )
        .join(VulnAdvisory, VulnAdvisory.id == VulnLink.advisory_id)
        .where(VulnLink.linked_at >= cutoff)
        .group_by(VulnLink.match_basis)
    ).all()

    by_match_basis = {str(basis): int(count) for basis, count in match_basis_rows}

    stale_advisories = int(
        db.execute(
            select(func.count(VulnLink.id))
            .join(VulnAdvisory, VulnAdvisory.id == VulnLink.advisory_id)
            .where(
                VulnLink.linked_at >= cutoff,
                VulnAdvisory.published_at.is_not(None),
                published_age_days > 365,
            )
        ).scalar_one()
    )

    return {
        "days": days,
        "total_links": total_links,
        "age_buckets": age_buckets,
        "by_severity": by_severity,
        "by_match_basis": by_match_basis,
        "stale_advisories": stale_advisories,
    }


def get_vuln_link_age_rollup(
    db: Session = Depends(get_session),
    days: int = 365,
) -> dict[str, Any]:
    return get_link_age_rollup(db=db, days=days)


def compute_link_age_rollup(
    db: Session = Depends(get_session),
    days: int = 365,
) -> dict[str, Any]:
    return get_link_age_rollup(db=db, days=days)


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
    VulnAdvisory.__table__.create(test_engine)
    VulnLink.__table__.create(test_engine)
    TestSession = sessionmaker(bind=test_engine, expire_on_commit=False)
    test_db = TestSession()

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    test_db.add_all(
        [
            VulnAdvisory(
                id=9001,
                affected_ranges=[],
                aliases=[],
                content_hash="test-advisory-9001",
                ecosystem="pypi",
                feed="test",
                fetched_at=now,
                identities=[],
                package="fresh-package",
                published_at=now - timedelta(days=10),
                severity="CRITICAL",
                source_url="https://example.invalid/advisory/9001",
                summary="Test advisory",
            ),
            VulnAdvisory(
                id=9002,
                affected_ranges=[],
                aliases=[],
                content_hash="test-advisory-9002",
                ecosystem="npm",
                feed="test",
                fetched_at=now,
                identities=[],
                package="old-package",
                published_at=now - timedelta(days=400),
                severity="HIGH",
                source_url="https://example.invalid/advisory/9002",
                summary="Test advisory",
            ),
        ]
    )
    test_db.flush()
    test_db.add_all(
        [
            VulnLink(
                id=9101,
                advisory_id=9001,
                linked_at=now - timedelta(days=1),
                match_basis="package",
                match_confidence=1.0,
                match_value="fresh-package",
                server_id="test-server-1",
            ),
            VulnLink(
                id=9102,
                advisory_id=9001,
                linked_at=now - timedelta(days=10),
                match_basis="package",
                match_confidence=0.9,
                match_value="fresh-package",
                server_id="test-server-2",
            ),
            VulnLink(
                id=9103,
                advisory_id=9002,
                linked_at=now - timedelta(days=45),
                match_basis="identity",
                match_confidence=0.8,
                match_value="old-package",
                server_id="test-server-3",
            ),
            VulnLink(
                id=9104,
                advisory_id=9002,
                linked_at=now - timedelta(days=120),
                match_basis="identity",
                match_confidence=0.7,
                match_value="old-package",
                server_id="test-server-4",
            ),
        ]
    )
    test_db.commit()

    test_app = FastAPI()

    @test_app.get("/api/vuln/link-age-rollup")
    def test_link_age_rollup(
        days: int = 365,
        db: Session = Depends(get_session),
    ) -> dict[str, Any]:
        return get_link_age_rollup(db=db, days=days)

    def override_get_session():
        yield test_db

    test_app.dependency_overrides[get_session] = override_get_session

    try:
        with TestClient(test_app) as client:
            response = client.get("/api/vuln/link-age-rollup", params={"days": 365})
            assert response.status_code == 200, response.text
            payload = response.json()
            assert payload["age_buckets"]["0-7"] == 1
            assert "CRITICAL" in payload["by_severity"]
            assert payload["total_links"] == 4
            assert payload["stale_advisories"] == 2
    finally:
        test_db.close()
        test_engine.dispose()

    print("PASS")