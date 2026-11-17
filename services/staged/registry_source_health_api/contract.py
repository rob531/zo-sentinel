from collections import defaultdict
from datetime import datetime
from typing import Dict, List, Optional

from fastapi import APIRouter, Depends, FastAPI
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry


class SourceHealth(BaseModel):
    registry_source: Optional[str]
    server_count: int
    avg_trust_score: Optional[float]
    verdicts: Dict[str, int]
    tiers: Dict[str, int]
    newest_last_seen: Optional[datetime]


class SourceHealthResponse(BaseModel):
    sources: List[SourceHealth]


def compute_source_health(session: Session) -> Dict[str, object]:
    rows = session.execute(select(McpServerRegistry)).scalars().all()

    grouped = defaultdict(list)
    for row in rows:
        grouped[row.registry_source].append(row)

    sources = []
    for registry_source, registry_rows in grouped.items():
        trust_values = [
            float(row.trust_score)
            for row in registry_rows
            if row.trust_score is not None
        ]
        verdicts = defaultdict(int)
        tiers = defaultdict(int)
        seen_dates = []

        for row in registry_rows:
            if row.verdict is not None:
                verdicts[str(row.verdict)] += 1
            if row.risk_tier is not None:
                tiers[str(row.risk_tier)] += 1
            if row.last_seen is not None:
                seen_dates.append(row.last_seen)

        sources.append(
            SourceHealth(
                registry_source=registry_source,
                server_count=len(registry_rows),
                avg_trust_score=(
                    sum(trust_values) / len(trust_values)
                    if trust_values
                    else None
                ),
                verdicts=dict(verdicts),
                tiers=dict(tiers),
                newest_last_seen=max(seen_dates) if seen_dates else None,
            )
        )

    sources.sort(key=lambda item: (item.registry_source is None, item.registry_source))
    return {"sources": sources}


router = APIRouter()


@router.get("/registry/source-health", response_model=SourceHealthResponse)
def source_health(session: Session = Depends(get_session)) -> Dict[str, object]:
    return compute_source_health(session)


app = FastAPI()
app.include_router(router, prefix="/api")


if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    McpServerRegistry.__table__.create(bind=engine)
    testing_session = sessionmaker(bind=engine, autoflush=False, autocommit=False)()

    testing_session.add_all(
        [
            McpServerRegistry(
                server_id="source-a-1",
                name="Source A One",
                registry_source="source-a",
                trust_score=90.0,
                verdict="trusted",
                risk_tier="low",
                last_seen=datetime(2024, 1, 1, 12, 0, 0),
            ),
            McpServerRegistry(
                server_id="source-a-2",
                name="Source A Two",
                registry_source="source-a",
                trust_score=70.0,
                verdict="review",
                risk_tier="medium",
                last_seen=datetime(2024, 1, 2, 12, 0, 0),
            ),
            McpServerRegistry(
                server_id="source-b-1",
                name="Source B One",
                registry_source="source-b",
                trust_score=80.0,
                verdict="trusted",
                risk_tier="low",
                last_seen=datetime(2024, 1, 3, 12, 0, 0),
            ),
            McpServerRegistry(
                server_id="source-b-2",
                name="Source B Two",
                registry_source="source-b",
                trust_score=60.0,
                verdict="blocked",
                risk_tier="high",
                last_seen=datetime(2024, 1, 4, 12, 0, 0),
            ),
        ]
    )
    testing_session.commit()

    def override_get_session():
        yield testing_session

    that_app = FastAPI()
    that_app.include_router(router, prefix="/api")
    that_app.dependency_overrides[get_session] = override_get_session

    with TestClient(that_app) as client:
        response = client.get("/api/registry/source-health")
        assert response.status_code == 200
        payload = response.json()
        assert len(payload["sources"]) == 2
        assert sum(item["server_count"] for item in payload["sources"]) == 4

    print("PASS")