"""smoke_entry.py -- CI-only entrypoint: boot the real app with auth
dependency-overrides against the seeded sqlite DB.

Does NOT modify shipped app code: it imports `app` from app/main.py and swaps
the Clerk-backed `get_principal` / `require_admin` dependencies for a fixed
admin principal via FastAPI's dependency_overrides. Clerk bootstrap in the HTML
is skipped because CLERK_PUBLISHABLE_KEY is forced empty (the __CLERK_PK__
placeholder never starts with pk_, so boot() runs without auth).

Usage:
    DATABASE_URL=sqlite:////tmp/smoke.db python tests/ci/smoke_entry.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("DATABASE_URL", "sqlite:////tmp/smoke.db")
os.environ["CLERK_PUBLISHABLE_KEY"] = ""   # force the no-Clerk boot path
os.environ.setdefault("APP_ENV", "dev")    # lifespan init_db() allowed
os.environ.pop("ASK_LLM", None)            # never call the ladder from CI

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.main import app  # noqa: E402
from verdict_breakdown_api import Principal, get_principal, require_admin  # noqa: E402

_CI_ADMIN = Principal(user_id="ci_admin", role="admin")

app.dependency_overrides[get_principal] = lambda: _CI_ADMIN
app.dependency_overrides[require_admin] = lambda: _CI_ADMIN


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=int(os.environ.get("SMOKE_PORT", "8010")),
                log_level="warning")
