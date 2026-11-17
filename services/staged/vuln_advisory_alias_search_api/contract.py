import json
from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

# Real data layer imports
from app.db import get_session
from app.models import VulnAdvisory, Base  # Base is the declarative base for table creation

router = APIRouter()


class AliasSearchResult(BaseModel):
    id: int
    feed: str
    summary: str
    severity: str
    ecosystem: str
    package: str
    aliases: List[str]
    source_url: Optional[str] = None
    published_at: datetime


class AliasSearchResponse(BaseModel):
    query: str
    total: int
    results: List[AliasSearchResult]


@router.get(
    "/api/vuln/aliases/search",
    response_model=AliasSearchResponse,
    summary="Search vulnerability advisory aliases",
)
def search_aliases(
    q: str = Query(..., description="Alias search term (case‑insensitive)"),
    limit: int = Query(50, ge=1, le=5000, description="Maximum number of results to return"),
    session: Session = Depends(get_session),
):
    if not q:
        raise HTTPException(status_code=400, detail="Query parameter 'q' is required")

    # Pull a generous batch from the DB (Postgres‑portable, no string interpolation)
    stmt = select(
        VulnAdvisory.id,
        VulnAdvisory.feed,
        VulnAdvisory.summary,
        VulnAdvisory.severity,
        VulnAdvisory.ecosystem,
        VulnAdvisory.package,
        VulnAdvisory.aliases,
        VulnAdvisory.source_url,
        VulnAdvisory.published_at,
    ).limit(5000)

    rows = session.execute(stmt).all()

    query_lc = q.lower()
    matches: List[AliasSearchResult] = []

    for (
        adv_id,
        feed,
        summary,
        severity,
        ecosystem,
        package,
        aliases_json,
        source_url,
        published_at,
    ) in rows:
        # aliases column is stored as JSON; ensure we have a list
        try:
            aliases = json.loads(aliases_json) if isinstance(aliases_json, str) else aliases_json
        except Exception:
            aliases = []

        if not isinstance(aliases, list):
            continue

        # case‑insensitive exact or substring match against any alias element
        if any(query_lc in (str(a).lower()) for a in aliases):
            matches.append(
                AliasSearchResult(
                    id=adv_id,
                    feed=feed,
                    summary=summary,
                    severity=severity,
                    ecosystem=ecosystem,
                    package=package,
                    aliases=aliases,
                    source_url=source_url,
                    published_at=published_at,
                )
            )
            if len(matches) >= limit:
                break

    return AliasSearchResponse(query=q, total=len(matches), results=matches)


# --------------------------------------------------------------------------- #
# Self‑test (executed with `python -m services.staged.vuln_advisory_alias_search_api.contract`)
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    import sys
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    from fastapi.testclient import TestClient

    # In‑memory SQLite engine (StaticPool for thread‑safety)
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine)

    # Seed data
    seed_session = SessionLocal()
    now = datetime.utcnow()
    adv1 = VulnAdvisory(
        id=1,
        feed="github",
        summary="Test advisory 1",
        severity="high",
        ecosystem="npm",
        package="package-one",
        aliases=json.dumps(["GHSA-abcd-1234"]),
        source_url="https://example.com/1",
        published_at=now,
        fetched_at=now,
        affected_ranges="[]",
        content_hash="hash1",
        identities="[]",
    )
    adv2 = VulnAdvisory(
        id=2,
        feed="nvd",
        summary="Test advisory 2",
        severity="medium",
        ecosystem="pypi",
        package="package-two",
        aliases=json.dumps(["CVE-2024-1111"]),
        source_url="https://example.com/2",
        published_at=now,
        fetched_at=now,
        affected_ranges="[]",
        content_hash="hash2",
        identities="[]",
    )
    adv3 = VulnAdvisory(
        id=3,
        feed="github",
        summary="Test advisory 3",
        severity="low",
        ecosystem="golang",
        package="package-three",
        aliases=json.dumps([]),
        source_url="https://example.com/3",
        published_at=now,
        fetched_at=now,
        affected_ranges="[]",
        content_hash="hash3",
        identities="[]",
    )
    seed_session.add_all([adv1, adv2, adv3])
    seed_session.commit()
    seed_session.close()

    # Build FastAPI app with dependency override
    app = FastAPI()
    app.include_router(router)

    def get_test_session() -> Session:
        return SessionLocal()

    app.dependency_overrides[get_session] = get_test_session

    client = TestClient(app)

    resp = client.get("/api/vuln/aliases/search", params={"q": "GHSA-abcd-1234"})
    if resp.status_code != 200:
        print(f"FAIL: unexpected status {resp.status_code}", file=sys.stderr)
        sys.exit(1)

    data = resp.json()
    if data.get("total") != 1:
        print(f"FAIL: expected total 1, got {data.get('total')}", file=sys.stderr)
        sys.exit(1)

    result = data["results"][0]
    if result["id"] != 1:
        print(f"FAIL: expected id 1, got {result['id']}", file=sys.stderr)
        sys.exit(1)

    if "severity" not in result or "package" not in result:
        print("FAIL: missing required fields in result", file=sys.stderr)
        sys.exit(1)

    print("PASS")
    sys.exit(0)