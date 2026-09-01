"""
Entity detail API logic module.

Provides a complete server entity profile by joining:
  - McpServerRegistry        (server row)
  - McpLlmAxisScore        (all 7 axes)
  - trust_gating_override      (verdict + reason)
  - vuln_link                  (OSV/GHSA count)

Uses the REAL app.db / app.models data layer. No inline or stub models.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from app.db import get_session
from app.models import (
    McpServerRegistry,
    McpLlmAxisScore,
    TrustGatingOverride,
    VulnLink,
)


# The 7 LLM-judged axes.
EXPECTED_AXES: tuple = (
    "tool_poison",
    "exfiltration",
    "auth_abuse",
    "supply_chain",
    "prompt_injection",
    "context_overflow",
    "spec_drift",
)


def _iso(value: Any) -> Optional[str]:
    """Return ISO-8601 string for datetime, pass through strings, None for None."""
    if value is None:
        return None
    if isinstance(value, str):
        return value
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.isoformat()
    return str(value)


def _parse_probs(probs: Any) -> Dict[str, Any]:
    """Parse probs JSON field (string or dict) into a dict."""
    if probs is None:
        return {}
    if isinstance(probs, dict):
        return probs
    if isinstance(probs, str):
        try:
            loaded = json.loads(probs)
            if isinstance(loaded, dict):
                return loaded
            return {"value": loaded}
        except Exception:
            return {}
    return {}


def _derive_trust_gate_verdict(url: Optional[str], name: Optional[str]) -> str:
    """Derive a trust gate verdict from url/name when no override row exists."""
    if not url and not name:
        return "unknown"
    if url:
        u = url.lower()
        if "localhost" in u or "127.0.0.1" in u:
            return "local_development"
        if u.startswith("https://"):
            return "trusted_origin"
    if name:
        n = name.lower()
        if any(tok in n for tok in ("test", "dev", "staging", "sandbox")):
            return "non_prod"
    return "unverified"


def _axis_to_payload(row: McpLlmAxisScore) -> Dict[str, Any]:
    """Convert an axis score row into its payload dict."""
    p_top = float(row.p_top) if getattr(row, "p_top", None) is not None else 0.0
    p_crit = float(row.p_critical) if getattr(row, "p_critical", None) is not None else 0.0
    p_danger = float(row.p_danger) if getattr(row, "p_danger", None) is not None else 0.0
    escalated = bool(p_top >= 0.5 or p_crit >= 0.3 or p_danger >= 0.5)
    return {
        "axis_name": row.axis_name,
        "label": row.label,
        "p_top": p_top,
        "p_critical": p_crit,
        "p_danger": p_danger,
        "escalated": escalated,
        "probs": _parse_probs(row.probs),
        "scored_at": _iso(row.scored_at),
    }


def get_entity_detail(server_id: int, session: Session) -> Optional[Dict[str, Any]]:
    """Return the full entity profile dict for a server, or None if not found."""
    server = session.get(McpServerRegistry, server_id)
    if server is None:
        return None

    axis_rows = (
        session.query(McpLlmAxisScore)
        .filter(McpLlmAxisScore.server_id == server_id)
        .order_by(McpLlmAxisScore.axis_name.asc())
        .all()
    )
    axes = [_axis_to_payload(r) for r in axis_rows]

    vuln_count = (
        session.query(VulnLink)
        .filter(VulnLink.server_id == server_id)
        .count()
    )

    override