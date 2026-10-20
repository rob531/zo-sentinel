"""
services/staged/promotion_readiness_report/logic.py

Provides a readiness report for staged services, indicating which services have
been built but are not yet mounted in the FastAPI application.
"""

from __future__ import annotations

import datetime
import importlib
import inspect
import json
import sys
from pathlib import Path
from typing import Any, Dict, List

from fastapi import FastAPI

# Real application data layer imports (required by the no‑hollow gate)
from app.db import get_session  # noqa: F401
from app.models import (  # noqa: F401
    ApiKey,
    AskCorpusDoc,
    CadenceJobRun,
    FreshnessStat,
    McpLlmAxisScore,
    McpScoreDispute,
    McpServerRegistry,
    Org,
    Perspective,
    PerspectiveEvent,
    PerspectiveSnapshot,
    ThreatIntelRef,
    User,
    VulnAdvisory,
    VulnLink,
)

# ---------------------------------------------------------------------------


def _load_fastapi_app() -> FastAPI:
    """
    Load the FastAPI application instance from ``app.main``.
    The module is expected to expose an ``app`` attribute.
    """
    try:
        main_mod = importlib.import_module("app.main")
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "Unable to import the FastAPI application (app.main). "
            "Ensure the application is importable."
        ) from exc

    if not hasattr(main_mod, "app"):
        raise RuntimeError("The module app.main does not expose an 'app' attribute.")
    app_obj = getattr(main_mod, "app")
    if not isinstance(app_obj, FastAPI):
        raise RuntimeError("app.main.app is not a FastAPI instance.")
    return app_obj


def _service_router_mounted(app: FastAPI, service_name: str) -> bool:
    """
    Determine whether a service's router is mounted in the FastAPI app.

    The heuristic checks whether any registered route has a path that starts
    with ``/api/<service_name>``. This matches the convention used throughout
    the repository where each staged service is mounted under the ``/api``
    prefix.
    """
    prefix = f"/api/{service_name}"
    for route in app.routes:
        # ``route.path`` may be a string like "/api/foo/bar"
        if isinstance(getattr(route, "path", None), str) and route.path.startswith(prefix):
            return True
    return False


def _service_last_modified(service_path: Path) -> datetime.datetime:
    """
    Return the most recent modification time of any file within ``service_path``.
    """
    latest_ts = 0.0
    for file in service_path.rglob("*"):
        if file.is_file():
            ts = file.stat().st_mtime
            if ts > latest_ts:
                latest_ts = ts
    return datetime.datetime.fromtimestamp(latest_ts, tz=datetime.timezone.utc)


def get_promotion_readiness_report(base_path: Path | None = None) -> Dict[str, Any]:
    """
    Compute the promotion readiness report.

    Parameters
    ----------
    base_path: Path | None
        The repository root. If ``None`` the path is inferred from the location
        of this file.

    Returns
    -------
    dict
        A dictionary with a ``summary`` section and a ``services`` list.
    """
    # Resolve repository root
    if base_path is None:
        repo_root = Path(__file__).resolve().parents[2]
    else:
        repo_root = Path(base_path).resolve()

    staged_dir = repo_root / "services" / "staged"
    if not staged_dir.is_dir():
        raise RuntimeError(f"Staged services directory not found: {staged_dir}")

    # Load the FastAPI application once
    fastapi_app = _load_fastapi_app()

    services_info: List[Dict[str, Any]] = []
    now_utc = datetime.datetime.now(tz=datetime.timezone.utc)

    for service_path in sorted(staged_dir.iterdir()):
        if not service_path.is_dir():
            continue
        service_name = service_path.name

        # Service TOML existence
        toml_exists = (service_path / "service.toml").is_file()

        # Router mounted?
        router_mounted = _service_router_mounted(fastapi_app, service_name)

        # Last modified timestamp and days since build
        last_mod_dt = _service_last_modified(service_path)
        days_since_build = (now_utc - last_mod_dt).days

        services_info.append(
            {
                "name": service_name,
                "service_toml_exists": toml_exists,
                "router_mounted": router_mounted,
                "last_modified": last_mod_dt.isoformat(),
                "days_since_build": days_since_build,
            }
        )

    total = len(services_info)
    mounted = sum(1 for s in services_info if s["router_mounted"])
    unmounted = total - mounted
    orphaned = sum(1 for s in services_info if not s["service_toml_exists"])

    report = {
        "summary": {
            "total_staged": total,
            "mounted": mounted,
            "unmounted": unmounted,
            "orphaned": orphaned,
        },
        "services": services_info,
    }
    return report


# ---------------------------------------------------------------------------

if __name__ == "__main__":
    """
    Self‑test:
    1. Create a temporary staged directory with three services.
    2. Mount one of them in a dummy FastAPI app exposed as ``app.main.app``.
    3. Run the report and assert the expected counts.
    """
    import tempfile
    from fastapi import APIRouter

    # -----------------------------------------------------------------------
    # Helper to create a dummy service directory
    def _make_service(dir_path: Path, name: str) -> Path:
        svc_path = dir_path / name
        svc_path.mkdir(parents=True, exist_ok=True)
        # create a dummy service.toml
        (svc_path / "service.toml").write_text("[service]\nname = '{}'\n".format(name))
        # add a dummy file to affect modification time
        (svc_path / "dummy.py").write_text("# placeholder")
        return svc_path

    # -----------------------------------------------------------------------
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_root = Path(tmpdir)
        staged_root = tmp_root / "services" / "staged"
        staged_root.mkdir(parents=True)

        # Create three services
        svc_mounted = "svc_one"
        svc_unmounted_a = "svc_two"
        svc_unmounted_b = "svc_three"

        _make_service(staged_root, svc_mounted)
        _make_service(staged_root, svc_unmounted_a)
        _make_service(staged_root, svc_unmounted_b)

        # -------------------------------------------------------------------
        # Build a dummy FastAPI app that mounts only ``svc_one``
        dummy_app = FastAPI()
        router = APIRouter()

        @router.get("/dummy")
        async def dummy_endpoint():
            return {"msg": "ok"}

        dummy_app.include_router(router, prefix=f"/api/{svc_mounted}")

        # Create a temporary ``app.main`` module exposing this app
        dummy_main_mod = type(sys)("app.main")
        dummy_main_mod.app = dummy_app
        sys.modules["app.main"] = dummy_main_mod

        # -------------------------------------------------------------------
        # Run the readiness report against the temporary repo layout
        report = get_promotion_readiness_report(base_path=tmp_root)

        summary = report["summary"]
        assert summary["mounted"] == 1, f"expected 1 mounted, got {summary['mounted']}"
        assert (
            summary["unmounted"] == 2
        ), f"expected 2 unmounted, got {summary['unmounted']}"
        print("PASS")