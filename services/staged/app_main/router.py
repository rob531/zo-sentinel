"""services/staged/app_main/router.py

Thin FastAPI router for the ``app_main`` service.

The router imports the real data layer (``app.db.get_session``) and
exposes the public callables defined in ``services.staged.app_main.logic``.
Only a minimal health endpoint is required for the test suite, but the
structure mirrors ``services/_exemplar/router.py`` so additional endpoints
can be added there without changing this file.
"""

from fastapi import APIRouter, Depends
from app.db import get_session

# Import the public logic functions.  The logic module must define a
# ``health_check`` callable that accepts a SQLAlchemy session and returns a
# JSON‑serialisable mapping.
from .logic import health_check

router = APIRouter()


@router.get("/health", tags=["health"])
def health_endpoint(session=Depends(get_session)):
    """
    Simple health endpoint that delegates to the service's business logic.

    Returns
    -------
    dict
        The result of ``health_check`` – typically a mapping containing status
        information.
    """
    return health_check(session)