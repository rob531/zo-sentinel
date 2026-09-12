from __future__ import annotations

import sys
from datetime import datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import Base, VulnAdvisory, VulnLink


router = APIRouter(prefix="/api", tags=["advisories"])


def _get_freshness_gate_data(db: Session) -> dict[str, Any]:
    today = datetime.utcnow()
    threshold_date = today - timedelta(days=90)

    results = (
        db.query(
            VulnAdvisory.id,
            VulnAdvisory.feed,
            VulnAdvisory.severity,
            VulnAdvisory.published_at,
        )
        .join(VulnLink, VulnAdvisory.id == VulnLink.advisory_id)
        .all()
    )

    if not results:
        results = db.query(
            VulnAdvisory.id,
            VulnAdvisory.feed,
            VulnAdvisory.severity,
            VulnAdvisory.published_at,
        ).all()

    feed_stats: dict[str, dict[str, Any]] = {}
    stale_advisories: list[dict[str, Any]] = []
    gated = False

    for adv_id, feed, severity, published_at in results:
        age_days = (today - published_at).days if published_at else 0
        is_stale = age_days > 90

        if feed not in feed_stats:
            feed_stats[feed] = {"total": 0, "stale_count": 0}
        feed_stats[feed]["total"] += 1
        if is_stale:
            feed_stats[feed]["stale_count"] += 1
            stale_advisories.append({
                "id": adv_id,
                "feed": feed,
                "severity": severity,
                "published_at": published_at.strftime("%Y-%m-%d") if published_at else None,
                "age_days": age_days,
            })
            gated = True

    feeds = []
    for feed, stats in feed_stats.items():
        total = stats["total"]
        stale_count = stats["stale_count"]
        freshness_pct = round((total - stale_count) / total * 100, 1)
        feeds.append({
            "feed": feed,
            "total": total,
            "stale_count": stale_count,
            "freshness_pct": freshness_pct,
        })

    return {
        "feeds": feeds,
        "stale_advisories": stale_advisories,
        "gated": gated,
    }


@router.get("/advisories/freshness-gate")
def get_freshness_gate(db: Session = Depends(get_session)) -> dict[str, Any]:
    return _get_freshness_gate_data(db)


def _run_self_test() -> None:
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    TestingSessionLocal = sessionmaker(bind=engine)

    db = TestingSessionLocal()

    now = datetime.utcnow()
    test_advisories = [
        VulnAdvisory(
            id="nvd-001", feed="nvd", severity="HIGH",
            published_at=now - timedelta(days=5), package="pkg-a",
            summary="", ecosystem="npm", source_url="", content_hash="",
        ),
        VulnAdvisory(
            id="nvd-002", feed="nvd", severity="MEDIUM",
            published_at=now - timedelta(days=100), package="pkg-b",
            summary="", ecosystem="pip", source_url="", content_hash="",
        ),
        VulnAdvisory(
            id="ghsa-001", feed="ghsa", severity="CRITICAL",
            published_at=now - timedelta(days=30), package="pkg-c",
            summary="", ecosystem="npm", source_url="", content_hash="",
        ),
        VulnAdvisory(
            id="ghsa-002", feed="ghsa", severity="LOW",
            published_at=now - timedelta(days=95), package="pkg-d",
            summary="", ecosystem="maven", source_url="", content_hash="",
        ),
        VulnAdvisory(
            id="osv-001", feed="osv", severity="HIGH",
            published_at=now - timedelta(days=20), package="pkg-e",
            summary="", ecosystem="cargo", source_url="", content_hash="",
        ),
        VulnAdvisory(
            id="osv-002", feed="osv", severity="MEDIUM",
            published_at=now - timedelta(days=105), package="pkg-f",
            summary="", ecosystem="go", source_url="", content_hash="",
        ),
    ]

    for adv in test_advisories:
        db.add(adv)
    db.commit()

    for adv in test_advisories:
        link = VulnLink(
            advisory_id=adv.id,
            match_value="test",
            match_basis="test",
            match_confidence=1.0,
            server_id="test-server",
            linked_at=now,
        )
        db.add(link)
    db.commit()

    def override_get_session():
        return db

    from fastapi import FastAPI

    app = FastAPI()
    app.dependency_overrides[get_session] = override_get_session
    app.include_router(router)

    client = TestClient(app)
    response = client.get("/api/advisories/freshness-gate")
    assert response.status_code == 200, f"Expected 200, got {response.status_code}"

    data = response.json()
    assert data["gated"] == True, f"Expected gated=True when advisories >90d old"
    assert len(data["stale_advisories"]) == 3

    feeds_data = {f["feed"]: f for f in data["feeds"]}
    assert feeds_data["nvd"]["total"] == 2
    assert feeds_data["nvd"]["stale_count"] == 1
    assert feeds_data["nvd"]["freshness_pct"] == 50.0

    assert feeds_data["ghsa"]["total"] == 2
    assert feeds_data["ghsa"]["stale_count"] == 1
    assert feeds_data["ghsa"]["freshness_pct"] == 50.0

    assert feeds_data["osv"]["total"] == 2
    assert feeds_data["osv"]["stale_count"] == 1
    assert feeds_data["osv"]["freshness_pct"] == 50.0

    db.close()

    print("PASS")
    sys.exit(0)


if __name__ == "__main__":
    _run_self_test()