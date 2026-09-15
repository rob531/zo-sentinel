"""
Daemon Health Observability Service
Provides daemon health overview and job status endpoints.
"""
from contextlib import asynccontextmanager
from typing import Any

from app.db import get_session
from app.models import McpServerRegistry
from fastapi import Depends, FastAPI
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session


class DaemonHealthOverview(BaseModel):
    """Overview of daemon health status."""
    total_daemons: int
    healthy_daemons: int
    unhealthy_daemons: int
    unknown_daemons: int


class DaemonJob(BaseModel):
    """Individual daemon job status."""
    daemon_id: str
    daemon_name: str
    status: str
    last_run: str | None


class DaemonJobsResponse(BaseModel):
    """Response model for daemon jobs endpoint."""
    jobs: list[DaemonJob]
    total: int


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Lifespan context manager for FastAPI app."""
    yield


def create_app() -> FastAPI:
    """Create and configure the FastAPI application."""
    app = FastAPI(
        title="Daemon Health Observability Service",
        description="Service for monitoring daemon health and job status",
        version="1.0.0",
        lifespan=lifespan,
    )

    @app.get("/api/daemon-health/overview", response_model=DaemonHealthOverview)
    def get_daemon_health_overview(session: Session = Depends(get_session)) -> DaemonHealthOverview:
        """
        Get overview of all daemon health status.
        
        Returns aggregated counts of daemons by health status.
        """
        servers = session.execute(select(McpServerRegistry)).scalars().all()
        
        healthy = 0
        unhealthy = 0
        unknown = 0
        
        for server in servers:
            status = getattr(server, 'health_status', None) or getattr(server, 'status', 'unknown')
            if status in ('healthy', 'running', 'active'):
                healthy += 1
            elif status in ('unhealthy', 'failed', 'error', 'stopped'):
                unhealthy += 1
            else:
                unknown += 1
        
        return DaemonHealthOverview(
            total_daemons=len(servers),
            healthy_daemons=healthy,
            unhealthy_daemons=unhealthy,
            unknown_daemons=unknown,
        )

    @app.get("/api/daemon-health/jobs", response_model=DaemonJobsResponse)
    def get_daemon_jobs(session: Session = Depends(get_session)) -> DaemonJobsResponse:
        """
        Get list of daemon jobs and their status.
        
        Returns all registered daemons with their job status information.
        """
        servers = session.execute(select(McpServerRegistry)).scalars().all()
        
        jobs = []
        for server in servers:
            daemon_id = getattr(server, 'id', None) or str(getattr(server, 'server_id', 'unknown'))
            daemon_name = getattr(server, 'name', None) or getattr(server, 'server_name', 'unknown')
            status = getattr(server, 'health_status', None) or getattr(server, 'status', 'unknown')
            last_run = getattr(server, 'last_health_check', None) or getattr(server, 'updated_at', None)
            last_run_str = last_run.isoformat() if last_run else None
            
            jobs.append(DaemonJob(
                daemon_id=str(daemon_id),
                daemon_name=str(daemon_name),
                status=str(status),
                last_run=last_run_str,
            ))
        
        return DaemonJobsResponse(
            jobs=jobs,
            total=len(jobs),
        )

    return app


app = create_app()


if __name__ == "__main__":
    import sys
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    # Create in-memory SQLite database for self-test
    test_engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    # Create all tables
    from app.models import Base
    Base.metadata.create_all(bind=test_engine)

    # Create test session
    TestSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)
    test_session = TestSessionLocal()

    # Override the dependency
    def override_get_session():
        try:
            yield test_session
        finally:
            pass

    test_app = create_app()
    test_app.dependency_overrides[get_session] = override_get_session

    client = TestClient(test_app)

    try:
        # Test /api/daemon-health/overview
        response = client.get("/api/daemon-health/overview")
        assert response.status_code == 200, f"Overview endpoint failed: {response.status_code}"
        overview_data = response.json()
        assert "total_daemons" in overview_data
        assert "healthy_daemons" in overview_data
        assert "unhealthy_daemons" in overview_data
        assert "unknown_daemons" in overview_data

        # Test /api/daemon-health/jobs
        response = client.get("/api/daemon-health/jobs")
        assert response.status_code == 200, f"Jobs endpoint failed: {response.status_code}"
        jobs_data = response.json()
        assert "jobs" in jobs_data
        assert "total" in jobs_data
        assert isinstance(jobs_data["jobs"], list)

        print("PASS")
        sys.exit(0)
    except Exception as e:
        print(f"FAIL: {e}")
        sys.exit(1)