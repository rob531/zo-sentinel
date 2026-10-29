from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, FastAPI
from pydantic import BaseModel
from sqlalchemy import asc, desc, func, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry


router = APIRouter(prefix="/api")


class RegistryScanCountSource(BaseModel):
    registry_source: Optional[str]
    servers: int
    total_scans: int
    avg_scans: float
    max_scans: int


class RegistryScanCountServer(BaseModel):
    server_id: str
    name: Optional[str]
    scan_count: int
    risk_tier: Optional[str]
    last_scanned: Optional[datetime]


class RegistryScanCountRollup(BaseModel):
    sources: List[RegistryScanCountSource]
    top_servers: List[RegistryScanCountServer]
    total_servers: int


def get_registry_scan_count_rollup(session: Session) -> RegistryScanCountRollup:
    source_rows = session.execute(
        select(
            McpServerRegistry.registry_source.label("registry_source"),
            func.count(McpServerRegistry.server_id).label("servers"),
            func.coalesce(
                func.sum(func.coalesce(McpServerRegistry.scan_count, 0)), 0
            ).label("total_scans"),
            func.coalesce(func.avg(McpServerRegistry.scan_count), 0).label(
                "avg_scans"
            ),
            func.coalesce(func.max(McpServerRegistry.scan_count), 0).label(
                "max_scans"
            ),
        )
        .group_by(McpServerRegistry.registry_source)
        .order_by(
            desc(
                func.coalesce(
                    func.sum(func.coalesce(McpServerRegistry.scan_count, 0)), 0
                )
            ),
            asc(McpServerRegistry.registry_source),
        )
    ).all()

    top_rows = session.execute(
        select(
            McpServerRegistry.server_id,
            McpServerRegistry.name,
            func.coalesce(McpServerRegistry.scan_count, 0).label("scan_count"),
            McpServerRegistry.risk_tier,
            McpServerRegistry.last_scanned,
        )
        .order_by(
            desc(func.coalesce(McpServerRegistry.scan_count, 0)),
            asc(McpServerRegistry.server_id),
        )
        .limit(20)
    ).all()

    sources = [
        RegistryScanCountSource(
            registry_source=row.registry_source,
            servers=int(row.servers or 0),
            total_scans=int(row.total_scans or 0),
            avg_scans=float(row.avg_scans or 0),
            max_scans=int(row.max_scans or 0),
        )
        for row in source_rows
    ]

    top_servers = [
        RegistryScanCountServer(
            server_id=str(row.server_id),
            name=row.name,
            scan_count=int(row.scan_count or 0),
            risk_tier=row.risk_tier,
            last_scanned=row.last_scanned,
        )
        for row in top_rows
    ]

    return RegistryScanCountRollup(
        sources=sources,
        top_servers=top_servers,
        total_servers=sum(source.servers for source in sources),
    )


@router.get(
    "/registry/scan-counts",
    response_model=RegistryScanCountRollup,
)
def registry_scan_counts(
    session: Session = Depends(get_session),
) -> RegistryScanCountRollup:
    return get_registry_scan_count_rollup(session)


app = FastAPI()
app.include_router(router)


def _self_test() -> None:
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    McpServerRegistry.__table__.create(engine)
    SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)

    db = SessionLocal()
    try:
        db.add_all(
            [
                McpServerRegistry(
                    server_id="server-zero",
                    name="Zero Scan Server",
                    registry_source="source-zero",
                    risk_tier="low",
                    scan_count=0,
                ),
                McpServerRegistry(
                    server_id="server-five",
                    name="Five Scan Server",
                    registry_source="source-five",
                    risk_tier="medium",
                    scan_count=5,
                ),
                McpServerRegistry(
                    server_id="server-twelve",
                    name="Twelve Scan Server",
                    registry_source="source-twelve",
                    risk_tier="high",
                    scan_count=12,
                ),
            ]
        )
        db.commit()
    finally:
        db.close()

    that_app = FastAPI()
    that_app.include_router(router)

    def override_get_session():
        session = SessionLocal()
        try:
            yield session
        finally:
            session.close()

    that_app.dependency_overrides[get_session] = override_get_session

    with TestClient(that_app) as client:
        response = client.get("/api/registry/scan-counts")
        assert response.status_code == 200
        payload = response.json()
        assert payload["total_servers"] == 3
        assert len(payload["sources"]) == 3
        assert sum(source["total_scans"] > 0 for source in payload["sources"]) == 2
        assert payload["top_servers"][0]["scan_count"] == 12

    print("PASS")


if __name__ == "__main__":
    _self_test()