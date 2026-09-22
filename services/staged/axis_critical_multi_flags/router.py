from __future__ import annotations

if __name__ == "__main__" and not __package__:
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
    __package__ = "services.staged.axis_critical_multi_flags"

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry

from .logic import get_servers_with_multiple_critical_axes

router = APIRouter(prefix="/api", tags=["axis_critical_multi_flags"])


class CriticalAxis(BaseModel):
    axis_name: str
    label: str | None = None
    p_critical: float | None = None


class FlaggedServer(BaseModel):
    server_id: str
    name: str | None = None
    risk_tier: str | None = None
    critical_count: int
    critical_axes: list[CriticalAxis]


class CriticalMultiFlagsResponse(BaseModel):
    servers: list[FlaggedServer]


@router.get("/axis/critical/multi-flag", response_model=CriticalMultiFlagsResponse)
def get_axis_critical_multi_flags(
    session: Session = Depends(get_session),
) -> CriticalMultiFlagsResponse:
    return CriticalMultiFlagsResponse(
        servers=get_servers_with_multiple_critical_axes(session),
    )


if __name__ == "__main__":
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.db import Base

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    TestingSessionLocal = sessionmaker(
        bind=engine,
        autoflush=False,
        autocommit=False,
    )

    axes = (
        "overall_risk",
        "auth_strength",
        "capability_breadth",
        "data_sensitivity",
        "network_egress",
        "maintainer_trust",
        "exploit_surface",
    )
    critical_by_server = {
        "server-1": {"overall_risk", "exploit_surface"},
        "server-2": {"overall_risk"},
        "server-3": set(),
    }

    with TestingSessionLocal() as db:
        db.add_all(
            [
                McpServerRegistry(
                    server_id="server-1",
                    name="Server One",
                    risk_tier="HIGH",
                ),
                McpServerRegistry(
                    server_id="server-2",
                    name="Server Two",
                    risk_tier="MEDIUM",
                ),
                McpServerRegistry(
                    server_id="server-3",
                    name="Server Three",
                    risk_tier="LOW",
                ),
            ]
        )
        score_id = 1
        for server_id, critical_axes in critical_by_server.items():
            for axis_name in axes:
                critical = axis_name in critical_axes
                db.add(
                    McpLlmAxisScore(
                        id=score_id,
                        server_id=server_id,
                        axis_name=axis_name,
                        label="CRITICAL" if critical else "NORMAL",
                        p_critical=0.9 if critical else 0.1,
                        model_version="self-test",
                    )
                )
                score_id += 1
        db.commit()

    def get_test_session():
        db = TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()

    test_app = FastAPI()
    test_app.include_router(router)
    test_app.dependency_overrides[get_session] = get_test_session

    response = TestClient(test_app).get("/api/axis/critical/multi-flag")
    assert response.status_code == 200, response.text
    payload = response.json()
    flagged = payload["servers"]
    assert len([server for server in flagged if server["critical_count"] == 2]) == 1
    print("PASS")