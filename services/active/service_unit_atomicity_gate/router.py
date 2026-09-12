# deps: fastapi, pydantic, sqlalchemy
"""service_unit_atomicity_gate -- verifies all staged services have complete file structure.

POST /api/internal/service-atomicity/check
  Returns per-service atomicity status: which required files are present/missing.

Public endpoint (auth=public).
Data: filesystem check of services/staged/ (no DB reads needed for the check itself;
      get_session is declared for the data-layer contract).
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import List

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.db import get_session

router = APIRouter(prefix="/api/internal", tags=["service-atomicity"])

REQUIRED_FILES = ["__init__.py", "logic.py", "router.py", "service.toml"]


class ServiceStatus(BaseModel):
    name: str = Field(description="Service directory name")
    status: str = Field(description="'complete' or 'incomplete'")
    missing_files: List[str] = Field(
        default_factory=list,
        description="List of missing required files"
    )


class AtomicityCheckResponse(BaseModel):
    services: List[ServiceStatus] = Field(
        description="Atomicity status of each discovered service"
    )
    total: int = Field(description="Total services checked")
    complete_count: int = Field(description="Number of complete services")
    incomplete_count: int = Field(description="Number of incomplete services")


def _check_service_atomicity(services_dir: Path) -> List[ServiceStatus]:
    """Check all service directories for complete file structure."""
    results: List[ServiceStatus] = []

    if not services_dir.exists():
        return results

    for entry in os.listdir(services_dir):
        service_path = services_dir / entry
        if not service_path.is_dir():
            continue
        if entry.startswith("_"):
            continue

        missing: List[str] = []
        for required_file in REQUIRED_FILES:
            if not (service_path / required_file).exists():
                missing.append(required_file)

        status = "complete" if not missing else "incomplete"
        results.append(ServiceStatus(name=entry, status=status, missing_files=missing))

    return results


@router.post(
    "/service-atomicity/check",
    response_model=AtomicityCheckResponse,
    summary="Check staged services for complete file structure",
)
def check_service_atomicity(
    db: Session = Depends(get_session),
) -> AtomicityCheckResponse:
    """
    Verify each service in services/staged/ has all required files:
    - __init__.py
    - logic.py
    - router.py
    - service.toml
    """
    current_file = Path(__file__).resolve()
    # services/active/service_unit_atomicity_gate -> services/active -> services -> parent
    services_dir = current_file.parent.parent.parent / "staged"

    service_statuses = _check_service_atomicity(services_dir)
    complete_count = sum(1 for s in service_statuses if s.status == "complete")
    incomplete_count = len(service_statuses) - complete_count

    return AtomicityCheckResponse(
        services=service_statuses,
        total=len(service_statuses),
        complete_count=complete_count,
        incomplete_count=incomplete_count,
    )


if __name__ == "__main__":
    import sys
    from pathlib import Path as _Path

    _repo_root = _Path(__file__).resolve().parents[2]
    if str(_repo_root) not in sys.path:
        sys.path.insert(0, str(_repo_root))

    import tempfile
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    # Build temp directory structure for self-test
    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir_path = _Path(tmpdir)

        complete = tmpdir_path / "complete_service"
        complete.mkdir()
        for f in REQUIRED_FILES:
            (complete / f).write_text("# test")

        partial = tmpdir_path / "partial_service"
        partial.mkdir()
        (partial / "__init__.py").write_text("# test")
        (partial / "logic.py").write_text("# test")

        partial2 = tmpdir_path / "partial_service2"
        partial2.mkdir()
        (partial2 / "service.toml").write_text("# test")

        original_check = _check_service_atomicity

        def mock_check(services_dir: _Path) -> List[ServiceStatus]:
            return original_check(tmpdir_path)

        import services.active.service_unit_atomicity_gate.router as _mod
        _mod._check_service_atomicity = mock_check

        test_app = FastAPI()
        test_app.include_router(router)

        test_engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        _TestSessionLocal = sessionmaker(bind=test_engine, expire_on_commit=False)

        def _override():
            db = _TestSessionLocal()
            try:
                yield db
            finally:
                db.close()

        from app.main import app as _main_app
        _main_app.dependency_overrides[get_session] = _override

        try:
            client = TestClient(test_app)
            response = client.post("/api/internal/service-atomicity/check")

            assert response.status_code == 200, f"Expected 200, got {response.status_code}"
            data = response.json()

            assert "services" in data
            assert "total" in data
            assert data["total"] == 3

            complete_svc = next(
                (s for s in data["services"] if s["name"] == "complete_service"), None
            )
            partial_svc = next(
                (s for s in data["services"] if s["name"] == "partial_service"), None
            )
            partial2_svc = next(
                (s for s in data["services"] if s["name"] == "partial_service2"), None
            )

            assert complete_svc is not None
            assert complete_svc["status"] == "complete"
            assert complete_svc["missing_files"] == []

            assert partial_svc is not None
            assert partial_svc["status"] == "incomplete"
            assert set(partial_svc["missing_files"]) == {"router.py", "service.toml"}

            assert partial2_svc is not None
            assert len(partial2_svc["missing_files"]) == 3
            assert "service.toml" not in partial2_svc["missing_files"]

            assert data["complete_count"] == 1
            assert data["incomplete_count"] == 2

            print("PASS")
        finally:
            _mod._check_service_atomicity = original_check
            _main_app.dependency_overrides.clear()
