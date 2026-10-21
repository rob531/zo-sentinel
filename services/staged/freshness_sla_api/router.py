from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db import get_session
from .logic import get_freshness_sla as build_freshness_sla

router = APIRouter()


@router.get("/freshness/sla")
def get_freshness_sla(session: Session = Depends(get_session)):
    return build_freshness_sla(session)


if __name__ == "__main__":
    from datetime import datetime, timedelta, timezone

    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.models import FreshnessStat, McpServerRegistry

    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    FreshnessStat.__table__.create(test_engine)
    McpServerRegistry.__table__.create(test_engine)
    TestingSessionLocal = sessionmaker(bind=test_engine)

    now = datetime.now(timezone.utc)
    surfaces = ["surface-alpha", "surface-beta", "surface-gamma"]
    servers = []

    for index, surface in enumerate(surfaces):
        aged = index == 0
        first_seen = now - timedelta(hours=40) if aged else now - timedelta(hours=2)
        computed_at = now - timedelta(hours=40) if aged else now
        server_id = f"server-{index}"
        servers.append(
            McpServerRegistry(
                name=surface,
                server_id=server_id,
                url=f"https://example.invalid/{server_id}",
                registry_source="contract",
                risk_tier="tier_1",
                first_seen=first_seen,
                last_seen=now,
                last_scanned=None if aged else now,
                last_assessed=None if aged else now,
                scan_count=0 if aged else 1,
            )
        )

    with TestingSessionLocal() as session:
        session.add_all(
            [
                FreshnessStat(
                    surface=surface,
                    payload={
                        "server": surface,
                        "server_name": surface,
                        "server_id": f"server-{index}",
                        "mcp_server_name": surface,
                    },
                    computed_at=now - timedelta(hours=40) if index == 0 else now,
                    compute_seconds=0.25 + index,
                )
                for index, surface in enumerate(surfaces)
            ]
        )
        session.add_all(servers)
        session.commit()

    test_app = FastAPI()
    test_app.include_router(router, prefix="/api")
    test_app.dependency_overrides[get_session] = lambda: TestingSessionLocal()

    with TestClient(test_app) as client:
        response = client.get("/api/freshness/sla")
        assert response.status_code == 200, response.text
        body = response.json()
        assert len(body["surfaces"]) == 3
        aged_surface = next(
            item for item in body["surfaces"] if item["surface"] == "surface-alpha"
        )
        assert aged_surface["stale"] is True
        rate = aged_surface["sla_24h_verdict"]["breach_rate"]
        assert isinstance(rate, float)
        assert 0.0 <= rate <= 1.0

    print("PASS")