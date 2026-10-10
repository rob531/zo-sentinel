#!/usr/bin/env python3
"""tools/score_deterministic/det_scorer.py -- deterministic, GPU-free axis scorer.

Given a server {name, url, source, description} it classifies each of the 7 risk
axes by posing that axis's rubric (rubrics.py) as a choice-classification to a
funded deterministic decider, and emits one row per axis IDENTICAL IN SHAPE to
`mcp_llm_axis_scores`:

    {server_id, axis_name, label, label_index, probs, p_top, p_critical, p_danger,
     escalated, escalated_to, decision_rule_version, model_version, adapter_sha256,
     scored_at}

so the output feeds the canonical tier calc (gate_rule_v1 + trust_gate) UNCHANGED.

Backends (pluggable via --backend):
  * glide  -- Fastino GLiDE, POST /v1/systemone, one `choice` question per axis.
              key: AgentVault `towersideglide` -> FASTINO_API_KEY (X-API-Key header).
  * jev    -- hosted P(yes)-per-row decider; key `jevapi`. One P(yes) query per
              candidate label, softmax-normalised into a class distribution so the
              SAME {label, p_top, probs} shape falls out. (Swap-in surrogate.)

NETWORK IS INJECTED: every backend takes a `transport` callable
(method, url, headers, json_body) -> dict. Production passes the urllib transport;
tests pass a fake that returns canned responses -> zero network / keys in-test.

NO prod writes here; this module only computes rows. Persistence is in
score_deterministic_run.py (DSN param, --apply gated).
"""
from __future__ import annotations

import argparse
import datetime
import json
import math
import os
import sys
from typing import Callable, Dict, List, Optional

# add repo root so we can reuse the canonical trust_gate (the anti-defamation cap).
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

try:  # package-relative when imported as tools.score_deterministic.det_scorer
    from .rubrics import (AXES, AXES_BY_NAME, ESCALATION_THRESHOLD,
                          OVERALL_RISK_AXIS, Axis, label_index, rubric_fingerprint)
except ImportError:  # script-relative when run directly
    from rubrics import (AXES, AXES_BY_NAME, ESCALATION_THRESHOLD,  # type: ignore
                         OVERALL_RISK_AXIS, Axis, label_index, rubric_fingerprint)

GLIDE_URL = "https://api.fastino.ai/v1/systemone"
GLIDE_MODEL_VERSION = "glide-systemone-v1"
JEV_MODEL_VERSION = "jev-pyes-v1"
DECISION_RULE_VERSION = "gate_rule_v1"

Transport = Callable[[str, str, Dict[str, str], Optional[dict]], dict]


# --------------------------------------------------------------------------- #
# transport                                                                   #
# --------------------------------------------------------------------------- #
def urllib_transport(method: str, url: str, headers: Dict[str, str],
                     body: Optional[dict]) -> dict:
    """Default real transport (stdlib only). Operator-run; not used in tests."""
    import urllib.request

    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=60) as resp:  # noqa: S310 (operator-run tool)
        return json.loads(resp.read().decode("utf-8"))


# --------------------------------------------------------------------------- #
# backends                                                                    #
# --------------------------------------------------------------------------- #
class Backend:
    """A backend returns, per axis, a class distribution over that axis's VERBATIM
    class set: {label: prob}. The scorer turns that into the stored row shape."""

    model_version = "unset"

    def classify(self, description: str, axis: Axis) -> Dict[str, float]:
        raise NotImplementedError


class GlideBackend(Backend):
    """Fastino GLiDE `/v1/systemone`, one `choice` question per axis.

    Request (confirmed against https://docs.fastino.ai/inference/systemone):
        POST /v1/systemone   header X-API-Key: <FASTINO_API_KEY>
        {"model":"fastino/GLiDE","state":<description>,
         "questions":{<axis>:{"type":"choice","instructions":...,"criteria":{...}}}}
    Response:
        {"model":"glide","answers":{<axis>:{"type":"choice","choice":<key>,
         "confidence":<float>,"probabilities":{<key>:<float>,...}}}}
    """

    model_version = GLIDE_MODEL_VERSION

    def __init__(self, transport: Transport, api_key: str = "",
                 url: str = GLIDE_URL):
        self.transport = transport
        self.api_key = api_key
        self.url = url

    def build_request(self, description: str, axis: Axis) -> dict:
        return {
            "model": "fastino/GLiDE",
            "state": description or "",
            "questions": {
                axis.name: {
                    "type": "choice",
                    "instructions": axis.instructions,
                    "criteria": dict(axis.criteria),
                }
            },
        }

    def classify(self, description: str, axis: Axis) -> Dict[str, float]:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["X-API-Key"] = self.api_key
        resp = self.transport("POST", self.url, headers,
                              self.build_request(description, axis))
        answer = (resp.get("answers") or {}).get(axis.name) or {}
        probs = answer.get("probabilities") or {}
        # keep only known classes; if the decider named a winner without full probs,
        # synthesise a peaked distribution from choice + confidence.
        dist = {c: float(probs.get(c, 0.0)) for c in axis.classes}
        if sum(dist.values()) <= 0.0:
            choice = answer.get("choice")
            conf = float(answer.get("confidence") or 1.0)
            if choice in dist:
                dist[choice] = conf
        return _normalise(dist, axis)


class JevBackend(Backend):
    """Hosted P(yes)-per-row decider (`jevapi`). jev scores one row at a time,
    returning P(yes). To reuse it as a multi-class axis classifier we ask, per
    candidate label, "P(yes) that this label applies" and softmax-normalise the
    per-label P(yes) scores into a class distribution -- yielding the identical
    {label, p_top, probs} shape GLiDE produces. Swap-in surrogate (--backend jev).

    Request shape is intentionally minimal and may need aligning with the live
    jevapi contract -- see the OPERATOR RUNBOOK. Transport is injected, so the
    exact wire shape is one edit away and fully mocked in-test.
    """

    model_version = JEV_MODEL_VERSION

    def __init__(self, transport: Transport, api_key: str = "",
                 url: str = "https://jev.invalid/v1/score"):
        self.transport = transport
        self.api_key = api_key
        self.url = url

    def build_request(self, description: str, axis: Axis, label: str) -> dict:
        return {
            "row": description or "",
            "question": f"{axis.instructions} Specifically: does the label "
                        f"'{label}' ({axis.criteria.get(label, '')}) apply?",
        }

    def classify(self, description: str, axis: Axis) -> Dict[str, float]:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        pyes: Dict[str, float] = {}
        for label in axis.classes:
            resp = self.transport("POST", self.url, headers,
                                  self.build_request(description, axis, label))
            pyes[label] = float(resp.get("p_yes", resp.get("probability", 0.0)))
        return _softmax(pyes, axis)


def _normalise(dist: Dict[str, float], axis: Axis) -> Dict[str, float]:
    total = sum(max(0.0, v) for v in dist.values())
    if total <= 0.0:
        n = len(axis.classes)
        return {c: 1.0 / n for c in axis.classes}
    return {c: max(0.0, dist.get(c, 0.0)) / total for c in axis.classes}


def _softmax(scores: Dict[str, float], axis: Axis) -> Dict[str, float]:
    vals = [scores.get(c, 0.0) for c in axis.classes]
    m = max(vals) if vals else 0.0
    exps = [math.exp(v - m) for v in vals]
    s = sum(exps) or 1.0
    return {c: e / s for c, e in zip(axis.classes, exps)}


def make_backend(name: str, transport: Optional[Transport] = None,
                 api_key: str = "") -> Backend:
    transport = transport or urllib_transport
    if name == "glide":
        return GlideBackend(transport, api_key=api_key)
    if name == "jev":
        return JevBackend(transport, api_key=api_key)
    raise ValueError(f"unknown backend: {name!r} (expected glide|jev)")


# --------------------------------------------------------------------------- #
# scoring                                                                      #
# --------------------------------------------------------------------------- #
def _p_critical(axis_name: str, dist: Dict[str, float]) -> Optional[float]:
    """Probability mass on CRITICAL for the overall_risk axis (drives escalation)."""
    if axis_name == OVERALL_RISK_AXIS:
        return float(dist.get("CRITICAL", 0.0))
    return None


def _p_danger(axis_name: str, dist: Dict[str, float]) -> Optional[float]:
    """Mass on the 'dangerous' top band (HIGH+CRITICAL) for overall_risk."""
    if axis_name == OVERALL_RISK_AXIS:
        return float(dist.get("HIGH", 0.0) + dist.get("CRITICAL", 0.0))
    return None


def score_axis(description: str, axis: Axis, backend: Backend,
               now_iso: str, adapter_sha: str) -> Dict:
    """Score ONE axis; return a dict shaped like a mcp_llm_axis_scores row
    (without server_id, which the caller stamps)."""
    dist = backend.classify(description, axis)
    # argmax is deterministic: ties broken by class-set order (first wins).
    label = max(axis.classes, key=lambda c: (dist.get(c, 0.0), -axis.classes.index(c)))
    p_top = float(dist.get(label, 0.0))
    p_crit = _p_critical(axis.name, dist)
    escalated = bool(axis.name == OVERALL_RISK_AXIS and p_crit is not None
                     and p_crit >= ESCALATION_THRESHOLD)
    return {
        "axis_name": axis.name,
        "label": label,
        "label_index": label_index(axis.name, label),
        "probs": {c: round(dist.get(c, 0.0), 6) for c in axis.classes},
        "p_top": round(p_top, 6),
        "p_critical": round(p_crit, 6) if p_crit is not None else None,
        "p_danger": round(_p_danger(axis.name, dist), 6)
                    if _p_danger(axis.name, dist) is not None else None,
        "escalated": escalated,
        "escalated_to": "CRITICAL" if escalated else None,
        "decision_rule_version": DECISION_RULE_VERSION,
        "model_version": backend.model_version,
        "adapter_sha256": adapter_sha,
        "scored_at": now_iso,
    }


def score_server(server: Dict, backend: Backend,
                 now_iso: Optional[str] = None) -> List[Dict]:
    """Score all 7 axes for a server {server_id?, name, url, source, description}.
    Returns 7 rows, each shaped like mcp_llm_axis_scores (with server_id stamped)."""
    now_iso = now_iso or datetime.datetime.utcnow().isoformat()
    adapter_sha = rubric_fingerprint()
    description = server.get("description") or ""
    sid = server.get("server_id") or server.get("id")
    rows = []
    for axis in AXES:
        row = score_axis(description, axis, backend, now_iso, adapter_sha)
        row["server_id"] = sid
        rows.append(row)
    return rows


# --------------------------------------------------------------------------- #
# tier calc -- REUSE the canonical gate_rule_v1 + trust_gate (do NOT reinvent) #
# --------------------------------------------------------------------------- #
def tier_from_rows(server: Dict, rows: List[Dict]) -> Dict:
    """Map the deterministic axis labels to a published risk_tier via the canonical
    gate_rule_v1 (escalated CRITICAL -> CRITICAL; else argmax overall_risk label)
    THEN the trust_gate anti-defamation cap. Mirrors apply_risk_tier_backfill.py
    verbatim; no composite, no tools/provenance (PR #85 skew lesson)."""
    from trust_gating_override import trust_gate  # repo-root canonical cap

    by_axis = {r["axis_name"]: r for r in rows}
    overall = by_axis.get(OVERALL_RISK_AXIS, {})
    # gate_rule_v1: escalated CRITICAL wins, else the argmax overall_risk label.
    if overall.get("escalated") and overall.get("escalated_to") == "CRITICAL":
        raw_tier = "CRITICAL"
    else:
        raw_tier = (overall.get("label") or "").upper()

    maint = (by_axis.get("maintainer_trust", {}).get("label") or "")
    gate = trust_gate(server.get("url"), server.get("name"),
                      {"overall_risk": raw_tier, "maintainer_trust": maint})
    return {
        "raw_tier": raw_tier,
        "published_tier": gate.get("published_overall_risk") or raw_tier,
        "capped": bool(gate.get("capped")),
        "trust_basis": gate.get("trust_basis"),
        "masquerade_flag": gate.get("masquerade_flag"),
    }


# --------------------------------------------------------------------------- #
# CLI                                                                          #
# --------------------------------------------------------------------------- #
def _read_server(args) -> Dict:
    if args.server_json:
        return json.loads(args.server_json)
    if args.description is not None:
        return {"server_id": args.server_id, "name": args.name, "url": args.url,
                "source": args.source, "description": args.description}
    # stdin JSON (object, or {"servers":[...]} handled by caller)
    return json.loads(sys.stdin.read())


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Deterministic MCP axis scorer (GPU-free).")
    ap.add_argument("--backend", choices=("glide", "jev"), default="glide")
    ap.add_argument("--server-json", help="a single server as a JSON object string")
    ap.add_argument("--description", help="score an ad-hoc server from this description")
    ap.add_argument("--server-id", default=None)
    ap.add_argument("--name", default=None)
    ap.add_argument("--url", default=None)
    ap.add_argument("--source", default=None)
    ap.add_argument("--with-tier", action="store_true",
                    help="also print the gate_rule_v1 + trust_gate published tier")
    ap.add_argument("--api-key-env", default=None,
                    help="env var holding the backend key (default FASTINO_API_KEY "
                         "for glide, JEV_API_KEY for jev)")
    a = ap.parse_args(argv)

    key_env = a.api_key_env or ("FASTINO_API_KEY" if a.backend == "glide" else "JEV_API_KEY")
    backend = make_backend(a.backend, transport=urllib_transport,
                           api_key=os.environ.get(key_env, ""))
    server = _read_server(a)
    rows = score_server(server, backend)
    out = {"server_id": server.get("server_id") or server.get("id"), "rows": rows}
    if a.with_tier:
        out["tier"] = tier_from_rows(server, rows)
    print(json.dumps(out, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
