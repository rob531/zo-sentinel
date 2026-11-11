from datetime import datetime, timedelta
from typing import Any, Dict

from fastapi import APIRouter, Depends, FastAPI, Query
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import get_session
from app.models import McpServerRegistry

from .logic import get_scan_staleness_lanes

router = APIRouter(prefix="/api", tags=["registry"])


@router.get("/registry/scan-staleness-lanes")
def scan_staleness_lanes(
    days: int = Query(30),
    db: Session = Depends(get_session),
) -> Dict[str, Any]:
    return get_scan_staleness_lanes(db, days)


if __name__ == "__main__":
    from fastapi.testclient import TestClient

    engine = create_engine(
        "sqlite+pysqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    McpServerRegistry.__table__.create(engine)
    local_session = sessionmaker(bind=engine)()

    now = datetime.utcnow()
    local_session.add_all(
        [
            McpServerRegistry(
                server_id="scan-staleness-never",
                name="Never scanned",
                registry_source="github",
                scan_count=0,
                last_scanned=None,
            ),
            McpServerRegistry(
                server_id="scan-staleness-10d",
                name="Ten days old",
                registry_source="github",
                scan_count=1,
                last_scanned=now - timedelta(days=10),
            ),
            McpServerRegistry(
                server_id="scan-staleness-45d",
                name="Forty-five days old",
                registry_source="internal",
                scan_count=1,
                last_scanned=now - timedelta(days=45),
            ),
            McpServerRegistry(
                server_id="scan-staleness-200d",
                name="Two hundred days old",
                registry_source="internal",
                scan_count=1,
                last_scanned=now - timedelta(days=200),
            ),
        ]
    )
    local_session.commit()

    that_app = FastAPI()
    that_app.include_router(router)

    def override_get_session():
        yield local_session

    that_app.dependency_overrides[get_session] = override_get_session

    with TestClient(that_app) as client:
        response = client.get("/api/registry/scan-staleness-lanes", params={"days": 30})
        response.raise_for_status()
        payload = response.json()

    assert payload["days"] == 30
    assert sum(payload["lanes"].values()) == 4
    assert payload["by_source"]
    print("PASS")