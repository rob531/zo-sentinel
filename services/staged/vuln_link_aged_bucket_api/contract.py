from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends
from fastapi.testclient import TestClient
from sqlalchemy import case, create_engine, func, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import VulnLink

router = APIRouter(prefix="/api")

BUCKET_NAMES = ("0-7d", "8-30d", "31-90d", "91-365d", "365d+")


def get_vuln_link_age_buckets(session: Session = Depends(get_session)) -> dict:
    anchor = datetime.now(timezone.utc)

    bucket_expression = case(
        (VulnLink.linked_at > anchor - timedelta(days=8), "0-7d"),
        (VulnLink.linked_at > anchor - timedelta(days=31), "8-30d"),
        (VulnLink.linked_at > anchor - timedelta(days=91), "31-90d"),
        (VulnLink.linked_at > anchor - timedelta(days=366), "91-365d"),
        else_="365d+",
    )

    bucket_rows = session.execute(
        select(bucket_expression, func.count())
        .select_from(VulnLink)
        .group_by(bucket_expression)
    ).all()

    basis_rows = session.execute(
        select(VulnLink.match_basis, func.count())
        .select_from(VulnLink)
        .group_by(VulnLink.match_basis)
    ).all()

    oldest_linked_at = session.execute(
        select(func.min(VulnLink.linked_at)).select_from(VulnLink)
    ).scalar_one_or_none()

    buckets = {name: 0 for name in BUCKET_NAMES}
    for bucket, count in bucket_rows:
        buckets[bucket] = int(count)

    by_match_basis = {
        basis: int(count)
        for basis, count in basis_rows
        if basis is not None
    }

    if oldest_linked_at is not None:
        if oldest_linked_at.tzinfo is None:
            oldest_linked_at = oldest_linked_at.replace(tzinfo=timezone.utc)
        oldest_linked_at = oldest_linked_at.isoformat()

    return {
        "generated_at": anchor.isoformat(),
        "buckets": buckets,
        "by_match_basis": by_match_basis,
        "total_links": sum(buckets.values()),
        "oldest_linked_at": oldest_linked_at,
    }


@router.get("/vuln-links/age-buckets")
def vuln_link_age_buckets(
    result: dict = Depends(get_vuln_link_age_buckets),
) -> dict:
    return result


def _run_self_test() -> None:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    VulnLink.__table__.create(engine)

    anchor = datetime.now(timezone.utc)
    rows = [
        VulnLink(
            id=1,
            advisory_id=1,
            server_id=1,
            match_basis="cve",
            match_value="CVE-2026-0001",
            match_confidence=0.95,
            linked_at=anchor - timedelta(days=1),
        ),
        VulnLink(
            id=2,
            advisory_id=2,
            server_id=2,
            match_basis="package",
            match_value="package-1",
            match_confidence=0.90,
            linked_at=anchor - timedelta(days=10),
        ),
        VulnLink(
            id=3,
            advisory_id=3,
            server_id=3,
            match_basis="hostname",
            match_value="host-3",
            match_confidence=0.85,
            linked_at=anchor - timedelta(days=45),
        ),
        VulnLink(
            id=4,
            advisory_id=4,
            server_id=4,
            match_basis="banner",
            match_value="banner-4",
            match_confidence=0.80,
            linked_at=anchor - timedelta(days=120),
        ),
        VulnLink(
            id=5,
            advisory_id=5,
            server_id=5,
            match_basis="cve",
            match_value="CVE-2026-0005",
            match_confidence=0.75,
            linked_at=anchor - timedelta(days=400),
        ),
    ]

    with Session(engine) as session:
        session.add_all(rows)
        session.commit()

    def override_get_session():
        with Session(engine) as session:
            yield session

    that_app = FastAPI()
    that_app.include_router(router)
    that_app.dependency_overrides[get_session] = override_get_session

    with TestClient(that_app) as client:
        response = client.get("/api/vuln-links/age-buckets")
        assert response.status_code == 200, response.text

        payload = response.json()
        assert payload["buckets"] == {
            "0-7d": 1,
            "8-30d": 1,
            "31-90d": 1,
            "91-365d": 1,
            "365d+": 1,
        }
        assert sum(payload["by_match_basis"].values()) == 5
        assert payload["total_links"] == 5
        assert payload["oldest_linked_at"] is not None

    engine.dispose()


if __name__ == "__main__":
    from fastapi import FastAPI

    _run_self_test()
    print("PASS")