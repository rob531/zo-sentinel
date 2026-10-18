"""Shadow-mode typed-decision composer for /app (docs/eval/JEV_TYPED_DECISIONS_EVAL.md).

What it does: when an analyst runs a search on /app, the page fires a best-effort
POST here with the text and the view the page actually showed. If shadow mode is
on, a background task asks zo_sentinel.decide.ui_compose which existing view it
WOULD have chosen and logs both. Nothing the analyst sees changes.

Off by default. On only when DECIDE_SHADOW=1 AND the backend's key is present
(OPENROUTER_API_KEY for jev, FASTINO_API_KEY for glide; DECIDE_SHADOW_BACKEND picks).
Bounded by DECIDE_SHADOW_MAX_PER_HOUR (default 200) per process. The decide package
is imported lazily so a missing module can never stop the app booting.

Readback: GET /api/admin/decide/shadow (admin) -> agreement, fallback rate, latency,
cost, and the most recent records. Every record is also logged as one JSON line
prefixed "decide_shadow" for durable collection from the platform logs.
"""
from __future__ import annotations

import json
import logging
import os
import statistics
import threading
import time
from collections import deque
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Depends
from pydantic import BaseModel, Field

from verdict_breakdown_api import Principal, get_principal, require_admin

log = logging.getLogger("decide_shadow")
router = APIRouter(prefix="/api", tags=["decide"])  # spine include_router() applies no prefix

_RECORDS: deque = deque(maxlen=500)
_LOCK = threading.Lock()
_WINDOW = {"start": 0.0, "count": 0}


class ShadowIn(BaseModel):
    q: str = Field(..., min_length=1, max_length=300)
    actual: str = Field(..., max_length=40)          # the view the page really rendered
    risk: Optional[str] = Field(None, max_length=16)
    server_id: Optional[str] = Field(None, max_length=200)


def _backend_name() -> str:
    return (os.environ.get("DECIDE_SHADOW_BACKEND") or "jev").strip().lower()


def _key_env(name: str) -> str:
    # Mirror JevBackend: JEV_BASE_URL pointing at api.typesafe.ai means the direct
    # TypeSafe key, otherwise the OpenRouter key. Must match or the switch reads OFF.
    if name == "glide":
        return "FASTINO_API_KEY"
    return "TYPESAFE_API_KEY" if "typesafe.ai" in os.environ.get("JEV_BASE_URL", "") else "OPENROUTER_API_KEY"


def shadow_enabled() -> bool:
    return os.environ.get("DECIDE_SHADOW") == "1" and bool(os.environ.get(_key_env(_backend_name())))


def _take_budget(now: float) -> bool:
    cap = int(os.environ.get("DECIDE_SHADOW_MAX_PER_HOUR", "200"))
    with _LOCK:
        if now - _WINDOW["start"] >= 3600:
            _WINDOW["start"], _WINDOW["count"] = now, 0
        if _WINDOW["count"] >= cap:
            return False
        _WINDOW["count"] += 1
        return True


def _run(item: ShadowIn, user_role: str) -> None:
    rec = {"ts": time.time(), "q": item.q, "actual": item.actual, "actual_risk": item.risk or "",
           "role": user_role, "backend": _backend_name()}
    try:
        from zo_sentinel.decide.backends import GlideBackend, JevBackend
        from zo_sentinel.decide.ui_compose import compose
        be = GlideBackend() if rec["backend"] == "glide" else JevBackend()
        spec = compose(item.q, be, server_id=item.server_id)
        rec.update({"view": spec["view"], "route": spec["route"], "filters": spec["filters"],
                    "panels": spec["panels"], "confidence": spec["confidence"], "fallback": spec["fallback"],
                    "view_match": spec["view"] == item.actual,
                    "risk_match": spec["filters"].get("risk", "") == (item.risk or "")})
    except Exception as e:  # never raise from a shadow path
        rec["error"] = type(e).__name__
    rec["elapsed_ms"] = round((time.time() - rec["ts"]) * 1000)
    with _LOCK:
        _RECORDS.append(rec)
    log.info("decide_shadow %s", json.dumps(rec, default=str))


@router.post("/decide/shadow")
def decide_shadow(item: ShadowIn, background: BackgroundTasks,
                  principal: Principal = Depends(get_principal)) -> dict:
    if not shadow_enabled():
        return {"shadow": "off"}
    if not _take_budget(time.time()):
        return {"shadow": "budget"}
    background.add_task(_run, item, principal.role)
    return {"shadow": "queued"}


def summarise(records) -> dict:
    ok = [r for r in records if "error" not in r]
    lat = [r["elapsed_ms"] for r in ok if "elapsed_ms" in r]
    return {
        "n": len(records),
        "errors": len(records) - len(ok),
        "view_agreement": round(sum(r["view_match"] for r in ok) / len(ok), 3) if ok else None,
        "fallback_rate": round(sum(r["fallback"] for r in ok) / len(ok), 3) if ok else None,
        "would_route_to": {v: sum(1 for r in ok if r["view"] == v) for v in sorted({r["view"] for r in ok})},
        "elapsed_ms_p50": round(statistics.median(lat)) if lat else None,
    }


@router.get("/admin/decide/shadow")
def decide_shadow_report(limit: int = 50, principal: Principal = Depends(require_admin)) -> dict:
    with _LOCK:
        records = list(_RECORDS)
    out = summarise(records)
    out.update({"enabled": shadow_enabled(), "backend": _backend_name(),
                "recent": records[-max(0, min(limit, 500)):]})
    return out
