"""
service_extraction_candidate_report.py

FastAPI module that reports candidate services by analysing orphan router modules
and live mounts. It reads data from the application Postgres database via the
SQLAlchemy session provided by ``app.db.get_session`` and the models defined in
``app.models``. The module is read‑only and does not perform any writes.

Public Interface
-----------------
GET /service_candidates
    Returns a list of ServiceCandidate objects.

Return Shape
------------
[
    {
        "service_name": str,
        "orphan_modules": [str, ...],
        "live_mounts": [str, ...]
    },
    ...
]
"""

from fastapi import FastAPI, APIRouter, Depends
from sqlalchemy.orm import Session
from typing import List

# Application data layer (must be used, never replaced outside tests)
from app.db import get_session
from app.models import AppSurfaceKl, SpineManifest  # type: ignore

app = FastAPI()
router = APIRouter()


# --------------------------------------------------------------------------- #
# Pydantic response model
# --------------------------------------------------------------------------- #
from pydantic import BaseModel


class ServiceCandidate(BaseModel):
    service_name: str
    orphan_modules: List[str]
    live_mounts: List[str]


# --------------------------------------------------------------------------- #
# Helper – minimal session for the self‑test
# --------------------------------------------------------------------------- #
class _MinimalSession:
    """A very small stand‑in for a SQLAlchemy Session used only in the __main__
    self‑test. ``query`` returns an empty list for any model, mimicking an empty
    database."""
    def query(self, *args, **kwargs):
        class _Query:
            def all(self_inner):
                return []
        return _Query()


# --------------------------------------------------------------------------- #
# Endpoint implementation
# --------------------------------------------------------------------------- #
@router.get(
    "/service_candidates",
    response_model=List[ServiceCandidate],
    summary="Report candidate services derived from orphan modules and live mounts",
)
def get_service_candidates(session: Session = Depends(get_session)):
    """
    Analyse router modules (reported by ``AppSurfaceKl``) and live mounts
    (reported by ``SpineManifest``) to produce a candidate service report.

    The logic is deliberately simple:
    * ``orphan_modules`` – modules appearing in routes but not in live mounts.
    * ``live_mounts`` – modules appearing in live mounts.
    * Each distinct orphan module becomes a ``ServiceCandidate`` whose
      ``service_name`` is the module name.
    """
    # ------------------------------------------------------------------- #
    # Extract module names from the two source tables
    # ------------------------------------------------------------------- #
    route_rows = session.query(AppSurfaceKl).all()
    mount_rows = session.query(SpineManifest).all()

    # The column that holds the module name may differ between tables.
    # We fall back to generic attribute names to stay robust.
    def _module_name(obj):
        for attr in ("module_name", "module", "name"):
            if hasattr(obj, attr):
                return getattr(obj, attr)
        return None

    route_modules = { _module_name(r) for r in route_rows if _module_name(r) }
    mount_modules = { _module_name(m) for m in mount_rows if _module_name(m) }

    # ------------------------------------------------------------------- #
    # Determine orphans and build candidates
    # ------------------------------------------------------------------- #
    orphan_modules = route_modules - mount_modules
    candidates: List[ServiceCandidate] = []

    for orphan in sorted(orphan_modules):
        candidates.append(
            ServiceCandidate(
                service_name=orphan,
                orphan_modules=[orphan],
                live_mounts=sorted(mount_modules),
            )
        )
    return candidates


app.include_router(router)


# --------------------------------------------------------------------------- #
# Self‑test
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    import json
    from fastapi.testclient import TestClient

    # Override the DB dependency with a minimal in‑memory session
    app.dependency_overrides[get_session] = lambda: _MinimalSession()

    client = TestClient(app)

    resp = client.get("/service_candidates")
    assert resp.status_code == 200, f"Unexpected status {resp.status_code}"
    data = resp.json()
    assert isinstance(data, list), "Response is not a list"
    # With the overridden empty session we expect an empty list
    assert data == [], f"Expected empty list, got {data!r}"

    print("PASS")