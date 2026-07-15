#!/usr/bin/env python3
"""
directive_knowledge_sources.py  —  Layer 1 prompt enrichment.

Provides three functions the directive generator's prompt builder can call
to inject grounded, live-system-state context into the MiniMax prompt.

  1. load_product_spec()      — static PRODUCT_SPEC.md content
  2. live_wiring_map()        — dynamic: daemons, tables, row counts, recent builds
  3. live_gaps_map()          — dynamic: spec-vs-reality diff

Philosophy:
  - Zero code changes outside sentinel_directive_generator.build_prompt().
  - Each function returns a plain string ready to inject into the prompt.
  - Graceful: if a data source is unavailable, return a short note, not an exception.
  - Budget: total added payload ~4-6k characters. Prompt headroom is large.
"""
import json
import logging
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

import requests

SENTINEL_DIR    = Path("/home/workspace/zo_sentinel")
PRODUCT_SPEC    = SENTINEL_DIR / "PRODUCT_SPEC.md"
# Machine-appended anchor extension (zo_sentinel/anchor_refill.py). Folded into
# the spec text below so the gaps-map extractor and the architect consume
# auto-mined candidates through the SAME pipe as human-authored ones. Deleting
# the file reverts every auto candidate; PRODUCT_SPEC.md itself stays
# human-owned.
AUTO_ANCHOR     = SENTINEL_DIR / "PRODUCT_SPEC_AUTO_ANCHOR.md"
WRITE_SERVICE   = "http://127.0.0.1:8772"

log = logging.getLogger("directive_knowledge_sources")


# ── helpers ────────────────────────────────────────────────────────────────

def _q(sql: str, params=None, timeout: int = 8) -> list:
    """Thin write_service /query wrapper. Returns [] on any failure."""
    body = {"sql": sql}
    if params:
        body["params"] = params
    try:
        r = requests.post(f"{WRITE_SERVICE}/query", json=body, timeout=timeout)
        if r.status_code == 200:
            return r.json().get("rows", [])
    except Exception as e:
        log.debug("query failed: %s", e)
    return []


# ── 1. Product spec (static) ─────────────────────────────────────────────────────

def load_product_spec() -> str:
    """Return the PRODUCT_SPEC.md contents, or a short 'missing' note."""
    if not PRODUCT_SPEC.exists():
        return (
            "[PRODUCT_SPEC.md missing]\n"
            "Spec not found. Treat all 'what to build next' proposals as guesses "
            "until a human creates /home/workspace/zo_sentinel/PRODUCT_SPEC.md."
        )
    try:
        spec = PRODUCT_SPEC.read_text()
    except Exception as e:
        log.warning("failed to read product spec: %s", e)
        return f"[PRODUCT_SPEC.md unreadable: {e}]"
    # Fold in the machine-appended auto-anchor (best-effort; absence or read
    # failure leaves the human spec exactly as before).
    try:
        if AUTO_ANCHOR.is_file():
            spec = spec + "\n" + AUTO_ANCHOR.read_text()
    except Exception as e:
        log.warning("auto-anchor unreadable (ignored): %s", e)
    return spec


# ── 2. Live wiring map ─────────────────────────────────────────────────────────

# Daemons we expect to be alive, with their expected max heartbeat age.
# Staleness threshold = 2x the cycle interval (allows for mid-cycle beats to age).
# Values grounded in observed behaviour from service_health rows 2026-04-18.
KNOWN_DAEMONS = [
    # High-frequency core (should be <5 min)
    ("write_service",                 300),    # note: self-heartbeat bug observed; stays here for visibility
    ("inference_router",              900),
    ("manager_agent",                 300),
    ("pipeline_bridge",               300),
    ("t2_consumer",                   300),
    ("zo_sentinel_builder",           600),    # polls every 5 min
    ("sentinel_directive_generator",  7500),   # polls every 2h + 5 min slack
    ("gate_scheduler",                180),    # heartbeats every 60s
    ("self_diagnostics",              600),
    ("build_watcher_api",             600),
    # Pipeline daemons (longer cycles)
    ("mcp_scanner",                   14400),  # 4h cycle
    ("signal_analyser",               7200),   # 2h
    ("trust_synthesiser",             3600),   # 30 min cycle, 1h slack
    ("threat_intel_ingestor",         14400),  # 2h + slack (but showed 2h)
    ("attestation_engine",            28800),  # 6h cycle + slack
    ("rug_pull_monitor",              28800),  # 6h + slack
    ("risk_ranker",                   21600),  # 4h + slack
    # Mesh-side, lower priority for sentinel purposes
    ("world_article_feeder",          3600),
    ("data_velocity",                 7200),
    ("anti_entropy",                  14400),
    ("wisdom_synthesiser",            14400),
    # Gate subsystem
    ("gate_orchestrator",             28800),  # fires every 6h by gate_scheduler
]

CORE_TABLES = [
    "mcp_server_registry", "mcp_signal_scores", "mcp_signal_enrichments",
    "mcp_threat_associations", "mcp_risk_register", "mcp_attestations",
    "mcp_definition_history", "mcp_submissions", "mcp_exemptions",
    "mcp_decisions", "mcp_policy_rules", "mcp_fingerprints",
    "mcp_tool_hashes",
]

# Tables that are legitimately empty until user / admin action populates them.
# Empty rows in these tables is NOT a pipeline failure — it's a new-install
# state. The directive generator must be told this explicitly or it will
# propose "fixes" for working infrastructure.
AWAITING_USER_TABLES = {
    "mcp_submissions",   # empty until a user submits an MCP via the portal
    "mcp_exemptions",    # empty until an admin grants an exemption
    "mcp_decisions",     # empty until approval_workflow runs
    "mcp_policy_rules",  # empty until an admin authors a policy
    "mcp_fingerprints",  # populates after mcp_fingerprinter cycles
    "mcp_tool_hashes",   # populates after mcp_scanner cycles
}


def _daemon_status() -> list[dict]:
    """Return rows of {service, age_sec, status, threshold_sec} for the known daemon set.
    Stale when age_sec > per-daemon threshold."""
    rows = _q(
        "SELECT service, "
        "CAST(EXTRACT(EPOCH FROM (now() - last_heartbeat)) AS INTEGER) AS age_sec, "
        "status "
        "FROM service_health"
    )
    by_name = {r["service"]: r for r in rows}
    out = []
    for name, threshold in KNOWN_DAEMONS:
        r = by_name.get(name)
        if r is None:
            out.append({"service": name, "age_sec": None,
                        "status": "never-seen", "threshold_sec": threshold})
            continue
        age = r.get("age_sec")
        status = r.get("status") or "unknown"
        if age is None:
            status_final = "never-seen"
        elif age > threshold:
            status_final = "stale"
        else:
            status_final = status if status != "unknown" else "healthy"
        out.append({
            "service":        name,
            "age_sec":        age,
            "status":         status_final,
            "threshold_sec":  threshold,
        })
    return out


def _table_counts() -> list[dict]:
    """Return {table, n_rows} for each core table. One query per table."""
    out = []
    for t in CORE_TABLES:
        rows = _q(f"SELECT COUNT(*) AS n FROM {t}")
        n = rows[0]["n"] if rows else None
        out.append({"table": t, "n": n})
    return out


def _recent_builds(limit: int = 20) -> list[dict]:
    """Recent successful builds with their interface signatures."""
    rows = _q(
        "SELECT content FROM mesh_memory "
        "WHERE agent_id = 't1.zo_sentinel_builder' "
        "AND content LIKE '%\"built_at\"%' "
        "ORDER BY created_at DESC LIMIT ?",
        [limit],
    )
    out = []
    for r in rows:
        try:
            d = json.loads(r.get("content", "{}"))
            if "file" in d:
                out.append({
                    "file": Path(d.get("file", "")).name,
                    "phase": d.get("phase", "?"),
                    "bytes": d.get("bytes", 0),
                    "interface": (d.get("interface") or "")[:120],
                    "built_at": (d.get("built_at") or "")[:19],
                })
        except json.JSONDecodeError:
            continue
    return out


def _fmt_age(age: int | None) -> str:
    if age is None:
        return "never"
    if age < 60:
        return f"{age}s"
    if age < 3600:
        return f"{age // 60}m"
    return f"{age // 3600}h{(age % 3600) // 60}m"


def live_wiring_map() -> str:
    """Human-readable snapshot of the live system. Ordered like a field report."""
    nl = chr(10)
    parts = ["## Live Wiring Map (as of " + datetime.now(timezone.utc).isoformat(timespec="seconds") + ")", ""]

    # Daemons
    parts.append("### Daemon heartbeats  (stale = age > per-daemon cycle threshold)")
    parts.append("  name                              age       status")
    for d in _daemon_status():
        age_str = _fmt_age(d["age_sec"])
        badge = ""
        if d["status"] == "never-seen":
            badge = "  <-- NEVER HEARTBEATED"
        elif d["status"] == "stale":
            badge = f"  <-- STALE (>{d['threshold_sec']}s)"
        parts.append(f"  {d['service']:<33} {age_str:<9} {d['status']}{badge}")
    parts.append("")

    # Tables
    parts.append("### Core table row counts")
    for t in _table_counts():
        n = t["n"]
        flag = ""
        if n is None:
            flag = "  <-- MISSING TABLE"
        elif n == 0:
            flag = "  <-- EMPTY"
        parts.append(f"  {t['table']:<32} {str(n) if n is not None else '?':>8}{flag}")
    parts.append("")

    # Recent builds — this is the interface-level wiring map
    parts.append("### Recently built files (newest 20, with interface signatures)")
    builds = _recent_builds(20)
    if not builds:
        parts.append("  (no recent builds found in mesh_memory)")
    else:
        for b in builds:
            parts.append(f"  [{b['built_at']}] {b['file']}")
            if b.get("interface"):
                parts.append(f"    iface: {b['interface']}")
    parts.append("")

    return nl.join(parts)


# ── 3. Live gaps map (spec vs reality) ───────────────────────────────────────────────

# Regex that pulls filename-style tokens out of the spec for the 'directive
# candidates' / 'not yet built' sections. Deliberately simple so spec authors
# can keep writing prose without special markup.
_CANDIDATE_FILENAME = re.compile(r"\b([a-z][a-z0-9_]{2,40}\.(?:py|html|md))\b")


def _spec_candidate_files(spec_text: str) -> list[str]:
    """Extract filenames mentioned near 'NOT YET', 'directive candidate',
    'Not yet built', 'NOT YET BUILT' — these are the spec's ask list."""
    out = []
    lines = spec_text.splitlines()
    for i, ln in enumerate(lines):
        low = ln.lower()
        if any(flag in low for flag in ("not yet", "directive candidate",
                                         "candidate:", "candidates:",
                                         "propose directives", "dormant")):
            # look at this line + the next 3 for filename-like tokens
            window = "\n".join(lines[i : i + 4])
            for m in _CANDIDATE_FILENAME.finditer(window):
                name = m.group(1)
                if name not in out:
                    out.append(name)
    return out


def _existing_filenames() -> set[str]:
    """Set of filenames the builder has registered as built (via mesh_memory)."""
    rows = _q(
        "SELECT DISTINCT content FROM mesh_memory "
        "WHERE agent_id = 't1.zo_sentinel_builder' "
        "AND content LIKE '%\"built_at\"%' "
        "ORDER BY created_at DESC LIMIT 500"
    )
    names = set()
    for r in rows:
        try:
            d = json.loads(r.get("content", "{}"))
            f = d.get("file")
            if f:
                names.add(Path(f).name)
        except json.JSONDecodeError:
            continue
    # Also include anything present on disk in the sentinel dir
    try:
        for p in SENTINEL_DIR.iterdir():
            if p.is_file() and p.suffix in (".py", ".html", ".md"):
                names.add(p.name)
    except Exception:
        pass
    return names


def _disk_filenames_recursive() -> set:
    """Filenames that exist ANYWHERE in the tree (recursive).

    The gaps map previously trusted mesh-memory registration only, so modules
    that exist on disk but were never registered (zo_sentinel/anchor_refill.py)
    showed as 'do NOT exist yet' forever -- phantom targets the architect kept
    proposing and the bridge kept rejecting (+0 mislabelled as non-convergence).
    Disk is truth: a file that exists is not a gap, wherever it lives."""
    skip = {".git", "__pycache__", "node_modules", ".venv", "directives",
            "logs", "backups"}
    names = set()
    try:
        for pp in SENTINEL_DIR.rglob("*"):
            if pp.is_file() and pp.suffix in (".py", ".html", ".md"):
                if any(part in skip for part in pp.parts):
                    continue
                names.add(pp.name)
    except Exception:
        pass
    return names


def live_gaps_map() -> str:
    """Cross-reference PRODUCT_SPEC.md's candidate list against live filesystem."""
    nl = chr(10)
    parts = ["## Live Gaps Map (spec candidates vs reality)", ""]

    spec = load_product_spec()
    if spec.startswith("[PRODUCT_SPEC.md"):
        parts.append("  (no spec available — gap analysis skipped)")
        return nl.join(parts)

    candidates = _spec_candidate_files(spec)
    existing   = _existing_filenames() | _disk_filenames_recursive()

    missing_files = [c for c in candidates if c not in existing]
    built_files   = [c for c in candidates if c in existing]

    parts.append("### Spec-named files that do NOT exist yet (primary directive targets)")
    if not missing_files:
        parts.append("  (none — spec candidate files are all present on disk)")
    else:
        for f in missing_files:
            parts.append(f"  - {f}")
    parts.append("")

    parts.append("### Spec-named files that exist (may need INTEGRATION, not rebuild)")
    if not built_files:
        parts.append("  (none)")
    else:
        for f in built_files:
            parts.append(f"  - {f}")
    parts.append("")

    # Daemon-level gaps: anything KNOWN_DAEMONS declares but is stale/never-seen
    dead = [d for d in _daemon_status() if d["status"] in ("never-seen", "stale")]
    if dead:
        parts.append("### Daemons declared in KNOWN_DAEMONS but stale or never-seen")
        for d in dead:
            parts.append(f"  - {d['service']}  (age={_fmt_age(d['age_sec'])}, status={d['status']})")
        parts.append("")

    # Empty core tables — split into two classes so generator reads them correctly
    empties = [t for t in _table_counts() if t["n"] == 0]
    awaiting   = [t for t in empties if t["table"] in AWAITING_USER_TABLES]
    pipe_gaps  = [t for t in empties if t["table"] not in AWAITING_USER_TABLES]
    if awaiting:
        parts.append("### Empty tables awaiting user/admin action (NORMAL — do NOT propose fixes)")
        for t in awaiting:
            parts.append(f"  - {t['table']}")
        parts.append("")
    if pipe_gaps:
        parts.append("### Empty tables indicating pipeline gap (INVESTIGATE)")
        for t in pipe_gaps:
            parts.append(f"  - {t['table']}")
        parts.append("")

    return nl.join(parts)


# ── Combined entry point for generator to call ─────────────────────────────────────

def live_quality_map() -> str:
    """Pull Gate 8 breaker state, quarantined files, and retry budgets.
    Generator uses this to avoid re-proposing known-bad rebuilds.
    Degrades gracefully if gate_quality_state is unavailable.
    """
    try:
        import sys as _sys
        if '/home/workspace/zo_sentinel' not in _sys.path:
            _sys.path.insert(0, '/home/workspace/zo_sentinel')
        import gate_quality_state as gqs
        snap = gqs.snapshot()
    except Exception as e:
        return (
            '## Quality map (Gate 8 breaker + quarantine)\n'
            f'- state unavailable: {e}\n'
            '- generator should proceed normally; do not treat this as a signal\n'
        )

    parts = ['## Quality map (Gate 8 circuit breaker + quarantine)']
    state = snap.get('state', 'closed')
    parts.append(f'- breaker_state: **{state}**')
    if snap.get('state_changed_reason'):
        parts.append(f'  (since {snap.get("state_changed_at")}: '
                     f'{snap.get("state_changed_reason")})')
    parts.append('')

    if state == 'tripped':
        parts.append('### !! BREAKER TRIPPED !!')
        parts.append('Generator MUST NOT propose rebuilds of any file listed')
        parts.append('under retry_budget or quarantine below. New/unrelated')
        parts.append('directives are still OK. Human must run reset_breaker.py')
        parts.append('to re-enable rebuilds.')
        parts.append('')
    elif state == 'half-open':
        parts.append('### breaker half-open')
        parts.append('Rebuilds permitted for ONE batch. Prefer conservative')
        parts.append('changes with explicit spec references.')
        parts.append('')

    q = snap.get('quarantined', {})
    if q:
        parts.append('### Quarantined files (DO NOT propose rebuilds)')
        for fn, meta in list(q.items())[:20]:
            reason = (meta.get('reason') or '')[:120]
            parts.append(f'  - `{fn}`  (at {meta.get("quarantined_at")}; {reason})')
        parts.append('')

    r = snap.get('file_retries', {})
    if r:
        parts.append(f'### Files failing Gate 8 (retry budget = {gqs.MAX_REBUILDS})')
        parts.append('If proposing a rebuild, you MUST reference the listed')
        parts.append('last_error and relevant spec section explicitly.')
        for fn, meta in list(r.items())[:20]:
            attempts = meta.get('attempts', 0)
            last_err = (meta.get('last_error') or '')[:140]
            parts.append(f'  - `{fn}`  attempts={attempts}/{gqs.MAX_REBUILDS}  '
                         f'last_error: {last_err}')
        parts.append('')

    cohorts = snap.get('recent_cohorts', [])
    if cohorts:
        parts.append('### Recent cohort fail rates (last 5)')
        for c in cohorts[-5:]:
            parts.append(f'  - {c.get("id")}: size={c.get("size")}  '
                         f'fail={c.get("fail_rate", 0):.0%}')
        parts.append('')

    if not q and not r:
        parts.append('- no failing files in retry accounting')
        parts.append('- no quarantined files')

    return '\n'.join(parts)


def assemble_layer1_context() -> dict[str, str]:
    """Returns a dict of prompt-ready sections. Generator inserts by key."""
    return {
        "product_spec":  load_product_spec(),
        "wiring_map":    live_wiring_map(),
        "gaps_map":      live_gaps_map(),
        "quality_map":   live_quality_map(),
    }


if __name__ == "__main__":
    # Self-test: print each section so a human can eyeball its shape.
    import sys
    ctx = assemble_layer1_context()
    for k, v in ctx.items():
        print(f"===== {k} =====")
        print(v)
        print()
    total = sum(len(v) for v in ctx.values())
    print(f"[self-test] total assembled chars: {total}", file=sys.stderr)