from __future__ import annotations

from collections import Counter
from typing import Any

from fastapi import Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry


_KEYWORDS = (
    "auth",
    "injection",
    "permission",
    "supply_chain",
    "network",
    "maintainer",
    "exploit",
    "unscored",
    "stale",
)

_KEYWORD_PATTERNS = {
    "auth": ("auth",),
    "injection": ("injection",),
    "permission": ("permission",),
    "supply_chain": ("supply_chain", "supply chain", "supply-chain"),
    "network": ("network",),
    "maintainer": ("maintainer",),
    "exploit": ("exploit",),
    "unscored": ("unscored", "un-scored"),
    "stale": ("stale",),
}


def _matched_keywords(reasoning: str | None) -> list[str]:
    if not reasoning:
        return []

    text = reasoning.casefold()
    matches = []
    for keyword in _KEYWORDS:
        if any(pattern in text for pattern in _KEYWORD_PATTERNS[keyword]):
            matches.append(keyword)
    return matches


def get_verdict_themes(
    session: Session = Depends(get_session),
    limit: int = 50,
) -> dict[str, Any]:
    limit = max(0, min(int(limit), 1000))

    rows = session.execute(
        select(
            McpServerRegistry.server_id,
            McpServerRegistry.verdict,
            McpServerRegistry.verdict_reasoning,
            McpServerRegistry.confidence,
            McpServerRegistry.risk_tier,
        )
        .order_by(McpServerRegistry.server_id)
        .limit(limit)
    ).all()

    total_servers = session.execute(
        select(func.count())
        .select_from(McpServerRegistry)
    ).scalar_one()

    per_verdict: dict[str, Counter[str]] = {}
    overall: Counter[str] = Counter()

    for row in rows:
        verdict = str(row.verdict) if row.verdict not in (None, "") else "unknown"
        keywords = _matched_keywords(row.verdict_reasoning)
        if not keywords:
            continue

        bucket = per_verdict.setdefault(verdict, Counter())
        for keyword in keywords:
            bucket[keyword] += 1
            overall[keyword] += 1

    tiers = {
        verdict: [
            {"keyword": keyword, "count": count}
            for keyword, count in sorted(counter.items())
        ]
        for verdict, counter in sorted(per_verdict.items())
    }

    top_keywords = [
        {"keyword": keyword, "count": count}
        for keyword, count in sorted(
            overall.items(),
            key=lambda item: (-item[1], item[0]),
        )
    ]

    return {
        "tiers": tiers,
        "top_keywords": top_keywords,
        "total_servers": int(total_servers or 0),
    }


get_verdict_theme_rollup = get_verdict_themes
get_verdict_reasoning_themes = get_verdict_themes
get_verdict_reasoning_theme_rollup = get_verdict_themes


if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine, insert
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    McpServerRegistry.__table__.create(engine)
    TestingSession = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    seeded_rows = [
        {
            "server_id": "server-1",
            "verdict": "malicious",
            "verdict_reasoning": "Authentication bypass permits unauthorized access.",
            "confidence": 0.99,
            "risk_tier": "critical",
        },
        {
            "server_id": "server-2",
            "verdict": "malicious",
            "verdict_reasoning": "A command injection exploit was observed.",
            "confidence": 0.98,
            "risk_tier": "critical",
        },
        {
            "server_id": "server-3",
            "verdict": "warning",
            "verdict_reasoning": "The package manager shows a supply chain concern.",
            "confidence": 0.75,
            "risk_tier": "medium",
        },
        {
            "server_id": "server-4",
            "verdict": "safe",
            "verdict_reasoning": "No known exploit or permission issue was found.",
            "confidence": 0.90,
            "risk_tier": "low",
        },
        {
            "server_id": "server-5",
            "verdict": "warning",
            "verdict_reasoning": "The registry metadata is stale.",
            "confidence": 0.60,
            "risk_tier": "medium",
        },
    ]

    with TestingSession() as session:
        session.execute(insert(McpServerRegistry), seeded_rows)
        session.commit()

    test_app = FastAPI()

    @test_app.get("/api/verdict/themes")
    def verdict_themes(
        limit: int = 50,
        session: Session = Depends(get_session),
    ):
        return get_verdict_themes(session=session, limit=limit)

    def override_get_session():
        with TestingSession() as session:
            yield session

    test_app.dependency_overrides[get_session] = override_get_session

    response = TestClient(test_app).get("/api/verdict/themes", params={"limit": 50})
    assert response.status_code == 200, response.text

    payload = response.json()
    assert any(
        item["keyword"] == "auth"
        for item in payload["tiers"]["malicious"]
    )
    assert payload["total_servers"] == 5
    assert payload["top_keywords"] == sorted(
        payload["top_keywords"],
        key=lambda item: (-item["count"], item["keyword"]),
    )

    print("PASS")