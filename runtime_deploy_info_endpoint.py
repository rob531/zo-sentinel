"""Deploy-info surface: /version (chairman-built 2026-07-14).

PURPOSE: pipeline-watch CHECK C (deploy drift) was unverifiable -- prod had no
/version surface (404 since the rc1 cut), so the watcher could not diff the
running build against origin/main. Returns the build identity baked into the
image at build time (GIT_SHA / BUILD_TIME build args, see Dockerfile) plus a
lightweight DB-liveness bit. Exposes no registry data; nothing signed/keyed.
"""
import os
from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db import get_session
from app.settings import settings

router = APIRouter(tags=["ops"])


@router.get("/version")
def version(db: Session = Depends(get_session)) -> dict:
    try:
        db.execute(text("SELECT 1"))
        db_reachable = True
    except Exception:
        db_reachable = False
    return {
        "app": settings.APP_NAME,
        "env": settings.ENV,
        "git_sha": os.environ.get("GIT_SHA", "unknown"),
        "built_at": os.environ.get("BUILD_TIME", "unknown"),
        "db_reachable": db_reachable,
        "reported_at": datetime.now(timezone.utc).isoformat(),
    }