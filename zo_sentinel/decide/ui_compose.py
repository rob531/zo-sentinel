"""Workday/Sana-style composition for the /app analyst UI -- SELECT, never generate.

An analyst types a question; compose() asks typed questions whose options are ONLY
views and panels that already exist in app/static/app.html, and returns a layout
spec the page maps straight onto its existing render functions. Nothing here emits
HTML. If the selector is unsure (p_top < floor) the spec falls back to the search
view seeded with the analyst's words -- the same thing the page does today -- so a
weak backend degrades to current behaviour, never to an invented screen.

Spec shape (A2UI-flavoured, deliberately tiny):
  {"view": "<view id>", "route": "/app/...", "panels": [...], "filters": {...},
   "confidence": {...}, "fallback": bool}
"""
from __future__ import annotations

from typing import Dict, Mapping, Optional

from .core import Choice, Noul, decide

# Every key maps to a render function that exists in app/static/app.html today.
VIEWS: Dict[str, Dict[str, str]] = {
    "dashboard": {"route": "/app", "fn": "viewDashboard",
                  "desc": "overall posture summary: totals, risk distribution, critical servers right now"},
    "explore": {"route": "/app/explore", "fn": "viewExplore",
                "desc": "search and filter the list of mcp servers by name, keyword or risk tier"},
    "detail": {"route": "/app/server/{server_id}", "fn": "viewDetail",
               "desc": "one specific named server: its verdict, axis scores, vulnerabilities, why it is risky"},
    "submit": {"route": "/app/submit", "fn": "viewSubmit",
               "desc": "submit add or request a new server url to be scanned assessed"},
    "reports": {"route": "/app/reports", "fn": "viewReports",
                "desc": "reports export trends and coverage statistics over time"},
}

RISK = {"ANY": "no particular risk tier mentioned",
        "CRITICAL": "critical most dangerous worst servers",
        "HIGH": "high risk servers",
        "MEDIUM": "medium moderate risk",
        "LOW": "low risk safe trusted servers"}

PANELS = {  # detail-view sub-panels that exist (vulns panel, dispute form, axis bars)
    "vulns": Noul("Does the analyst want vulnerabilities / CVEs for this server?",
                  yes="vulnerabilities cve cves exploit patch advisory",
                  no="no mention of vulnerabilities"),
    "dispute": Noul("Does the analyst want to dispute or challenge a verdict?",
                    yes="dispute challenge wrong incorrect appeal disagree verdict",
                    no="accepts the verdict, no dispute"),
}


def questions(floor: float = 0.45):
    return {
        "view": Choice("Which existing screen answers the analyst's request?",
                       {k: v["desc"] for k, v in VIEWS.items()}, floor=floor),
        "risk": Choice("Which risk tier, if any, is the analyst filtering to?", RISK, floor=floor),
        **PANELS,
    }


def compose(query: str, backend, *, server_id: Optional[str] = None,
            floor: float = 0.45, debias: bool = False) -> Mapping:
    state = {"analyst_request": query}
    if server_id:
        state["selected_server_id"] = server_id
    a = decide(state, questions(floor), backend, debias=debias)
    conf = {k: v.p_top for k, v in a.items()}
    view = a["view"].value
    if view == "detail" and not server_id:
        view = None  # cannot open a server the analyst did not identify
    if view is None:
        return {"view": "explore", "route": "/app/explore", "panels": [],
                "filters": {"q": query}, "confidence": conf, "fallback": True}
    route = VIEWS[view]["route"].replace("{server_id}", server_id or "")
    filters = {}
    if a["risk"].value not in (None, "ANY"):
        filters["risk"] = a["risk"].value
    panels = [p for p in PANELS if view == "detail" and a[p].yes]
    return {"view": view, "route": route, "panels": panels, "filters": filters,
            "confidence": conf, "fallback": False}
