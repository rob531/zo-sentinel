"""tools/score_deterministic/rubrics.py -- the 7 risk axes, their VERBATIM class
sets (from the v3.0 student's training rubric), the per-class criteria the
deterministic decider is asked to choose among, and the ordinal ordering used for
neighbour-agreement.

The class sets are copied verbatim from the session brief (which took them from the
student's training rubric). Do NOT reorder or rename: `label_index` is the index of
the chosen label INTO this list, so the order is part of the contract that keeps the
output shape identical to `mcp_llm_axis_scores`.

Pure data + helpers -- no I/O, no deps -> importable and unit-testable in isolation.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple


@dataclass(frozen=True)
class Axis:
    name: str
    # class set, VERBATIM and ORDER-SIGNIFICANT (label_index indexes into this).
    classes: Tuple[str, ...]
    # short instruction posed to the decider for this axis.
    instructions: str
    # per-class description shown to the decider (the "criteria" map for a choice q).
    criteria: Dict[str, str]
    # True when the class set is an ordinal ladder (least -> most risk) so a
    # neighbour (|idx_pred - idx_true| == 1) counts as near-agreement. The trailing
    # UNKNOWN sentinel, where present, is NOT part of the ladder.
    ordinal: bool
    # index past which labels are non-ordinal sentinels (e.g. UNKNOWN). Labels at
    # or after this index are excluded from neighbour scoring. None => all ordinal.
    ordinal_len: Optional[int] = None


# --- The 7 axes. Class sets are VERBATIM from the brief / student rubric. ---------

AXES: Tuple[Axis, ...] = (
    Axis(
        name="auth_strength",
        classes=("STRONG", "MODERATE", "WEAK", "UNKNOWN"),
        instructions=(
            "Judging ONLY from the server description, how strong is the "
            "authentication / authorization the server requires or enforces?"
        ),
        criteria={
            "STRONG": "Robust auth: OAuth2/OIDC, SSO, scoped API keys, mTLS, or per-request signed credentials.",
            "MODERATE": "Some auth present but limited: a single shared API key or basic token.",
            "WEAK": "Little or no auth: anonymous access, no credentials, or trivially bypassable.",
            "UNKNOWN": "The description gives no evidence either way about authentication.",
        },
        ordinal=True,
        ordinal_len=3,  # STRONG..WEAK ordinal; UNKNOWN is a sentinel
    ),
    Axis(
        name="capability_breadth",
        classes=("NARROW", "MODERATE", "BROAD", "UNKNOWN"),
        instructions=(
            "From the description, how broad is the set of capabilities / tools "
            "this server exposes?"
        ),
        criteria={
            "NARROW": "A single focused capability or a few tightly-scoped read-only tools.",
            "MODERATE": "A handful of related tools spanning read and limited write.",
            "BROAD": "Many tools, or sweeping capability (shell/exec, file system, arbitrary code, admin).",
            "UNKNOWN": "The description does not reveal what the server can do.",
        },
        ordinal=True,
        ordinal_len=3,
    ),
    Axis(
        name="data_sensitivity",
        classes=("PUBLIC", "INTERNAL", "SENSITIVE", "CRITICAL", "UNKNOWN"),
        instructions=(
            "From the description, how sensitive is the data this server can read "
            "or write?"
        ),
        criteria={
            "PUBLIC": "Only public / open data (public web, open datasets, published docs).",
            "INTERNAL": "Internal business data not meant for the public but not highly sensitive.",
            "SENSITIVE": "Personal data (PII), credentials, financial, or health data.",
            "CRITICAL": "Highly regulated or mission-critical data: secrets, payment rails, production databases, infra control.",
            "UNKNOWN": "The description gives no indication of the data's sensitivity.",
        },
        ordinal=True,
        ordinal_len=4,
    ),
    Axis(
        name="network_egress",
        classes=("NONE", "INTERNAL", "EXTERNAL", "ARBITRARY", "UNKNOWN"),
        instructions=(
            "From the description, what network egress (outbound connections) can "
            "this server make?"
        ),
        criteria={
            "NONE": "No outbound network access; operates purely locally / offline.",
            "INTERNAL": "Outbound only to internal / same-tenant services.",
            "EXTERNAL": "Outbound to specific, named external services or APIs.",
            "ARBITRARY": "Can reach arbitrary / user-controlled URLs or hosts (fetch, proxy, SSRF surface).",
            "UNKNOWN": "The description gives no indication of outbound network behaviour.",
        },
        ordinal=True,
        ordinal_len=4,
    ),
    Axis(
        name="maintainer_trust",
        # Brief: "ESTABLISHED|COMMUNITY|UNKNOWN_AUTHOR (+ any seen in data)".
        # VERIFIED is accepted on input by trust_gate; keep it as an allowed class
        # so stored student labels that use it still align on validation.
        classes=("ESTABLISHED", "VERIFIED", "COMMUNITY", "UNKNOWN_AUTHOR"),
        instructions=(
            "From the description, how established / trustworthy is the maintainer "
            "or publisher of this server?"
        ),
        criteria={
            "ESTABLISHED": "A well-known company, official vendor, or long-standing reputable project.",
            "VERIFIED": "An identity-verified publisher or verified organisation.",
            "COMMUNITY": "A community / open-source author without official vendor backing.",
            "UNKNOWN_AUTHOR": "No identifiable or credible maintainer in the description.",
        },
        ordinal=False,
    ),
    Axis(
        name="exploit_surface",
        classes=("MINIMAL", "LIMITED", "MODERATE", "BROAD"),
        instructions=(
            "From the description, how large is the exploitable attack surface of "
            "this server?"
        ),
        criteria={
            "MINIMAL": "Tiny surface: read-only, no untrusted input, no dangerous primitives.",
            "LIMITED": "Small surface: limited inputs, no code/command execution.",
            "MODERATE": "Noticeable surface: handles untrusted input, some write or integration risk.",
            "BROAD": "Large surface: code/command execution, file access, injection-prone, or many entry points.",
        },
        ordinal=True,
        ordinal_len=4,
    ),
    Axis(
        name="overall_risk",
        classes=("LOW", "MEDIUM", "HIGH", "CRITICAL"),
        instructions=(
            "Taking the whole description into account, what is the overall security "
            "risk tier of adopting this MCP server?"
        ),
        criteria={
            "LOW": "Low risk: narrow, public, well-maintained, minimal surface.",
            "MEDIUM": "Moderate risk: some sensitive capability or data but bounded.",
            "HIGH": "High risk: broad capability, sensitive data, or external egress combine.",
            "CRITICAL": "Critical risk: dangerous primitives over sensitive/critical data with arbitrary egress.",
        },
        ordinal=True,
        ordinal_len=4,
    ),
)

AXES_BY_NAME: Dict[str, Axis] = {a.name: a for a in AXES}

# The axis whose CRITICAL mass drives escalation in gate_rule_v1.
OVERALL_RISK_AXIS = "overall_risk"
# gate_rule_v1 escalation threshold (mirrors apply_risk_tier_backfill / gate_rule_v1_2026-06-16).
ESCALATION_THRESHOLD = 0.40


def label_index(axis_name: str, label: str) -> Optional[int]:
    """Index of `label` into the axis's VERBATIM class set, or None if unknown."""
    ax = AXES_BY_NAME.get(axis_name)
    if ax is None:
        return None
    try:
        return ax.classes.index(label)
    except ValueError:
        return None


def rubric_fingerprint() -> str:
    """Deterministic sha256 over the full rubric spec. Used as the
    `adapter_sha256`-equivalent tag so a change to any class set / instruction /
    criterion is visible in the stored provenance (the student uses a LoRA adapter
    sha; the deterministic scorer has no weights, so its 'adapter' IS the rubric)."""
    spec = [
        {"name": a.name, "classes": list(a.classes),
         "instructions": a.instructions, "criteria": a.criteria}
        for a in AXES
    ]
    blob = json.dumps(spec, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return "det-" + hashlib.sha256(blob).hexdigest()[:40]


def neighbour_ok(axis_name: str, pred: str, truth: str) -> Optional[bool]:
    """For an ORDINAL axis, True iff pred and truth are within one ladder step.
    Returns None when the axis is non-ordinal, or when either label is a
    non-ordinal sentinel (e.g. UNKNOWN) -- callers then fall back to exact match."""
    ax = AXES_BY_NAME.get(axis_name)
    if ax is None or not ax.ordinal:
        return None
    lim = ax.ordinal_len if ax.ordinal_len is not None else len(ax.classes)
    pi, ti = label_index(axis_name, pred), label_index(axis_name, truth)
    if pi is None or ti is None or pi >= lim or ti >= lim:
        return None
    return abs(pi - ti) <= 1
