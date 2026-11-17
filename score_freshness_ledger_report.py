"""score_freshness_ledger_report.py -- offline Markdown ledger of scoring freshness.

For every server in the registry it reports how fresh the LLM axis scoring is
(the newest `overall_risk` axis row defines `last_scored_at`) so freshness gaps
can be reviewed offline and archived from CI.

Data comes from the REAL app data layer -- `app.db` SQLAlchemy session over the
authoritative APP tables `mcp_server_registry` (McpServerRegistry) and
`mcp_llm_axis_scores` (McpLlmAxisScore). Read-only: no DB writes, no network
calls, no duckdb, no CSV/JSON inputs.

Pure part (stdlib only): render_markdown(rows).
Impure part: fetch_rows(session) reads Postgres through the app session.
"""
from __future__ import annotations

import argparse
import os
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry
from trust_gating_override import trust_gate

STALE_HOURS = 24
VERY_STALE_HOURS = 168

_AXES_OF_INTEREST = ("overall_risk", "maintainer_trust")

COLUMNS = ("server_id", "name", "registry_source", "risk_tier",
           "last_scored_at", "age_hours", "state")


# ---------------------------------------------------------------- helpers


def _parse_dt(value: Any) -> Optional[datetime]:
    """Coerce a datetime / ISO string into an aware UTC datetime (None if absent)."""
    if value is None:
        return None
    if isinstance(value, datetime):
        dt = value
    else:
        try:
            dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _cell(value: Any) -> str:
    """Markdown-safe cell value."""
    if value is None or value == "":
        return "-"
    if isinstance(value, float):
        text = f"{value:.1f}"
        return text
    return (str(value).replace("\\", "\\\\").replace("|", "\\|")
            .replace("\r", " ").replace("\n", " "))


def _fmt_ts(value: Any) -> str:
    dt = _parse_dt(value)
    return dt.isoformat() if dt else "-"


def _row_get(row: Any, key: str, default: Any = None) -> Any:
    if isinstance(row, dict):
        return row.get(key, default)
    return getattr(row, key, default)


# ---------------------------------------------------------------- data access


def fetch_rows(session: Session) -> List[Dict[str, Any]]:
    """Read registry + latest axis-score freshness for every server (read-only)."""
    registry = session.execute(
        select(McpServerRegistry.server_id,
               McpServerRegistry.name,
               McpServerRegistry.url,
               McpServerRegistry.registry_source,
               McpServerRegistry.risk_tier,
               McpServerRegistry.last_assessed)
    ).all()

    scores = session.execute(
        select(McpLlmAxisScore.server_id,
               McpLlmAxisScore.axis_name,
               McpLlmAxisScore.label,
               McpLlmAxisScore.scored_at)
        .where(McpLlmAxisScore.axis_name.in_(_AXES_OF_INTEREST))
    ).all()

    # newest row per (server, axis) -- `overall_risk` defines last_scored_at
    newest: Dict[str, Dict[str, Any]] = {}
    for server_id, axis_name, label, scored_at in scores:
        bucket = newest.setdefault(server_id, {})
        current = bucket.get(axis_name)
        if current is None:
            bucket[axis_name] = {"label": label, "scored_at": scored_at}
            continue
        cur_dt = _parse_dt(current.get("scored_at"))
        new_dt = _parse_dt(scored_at)
        if new_dt is not None and (cur_dt is None or new_dt > cur_dt):
            bucket[axis_name] = {"label": label, "scored_at": scored_at}

    rows: List[Dict[str, Any]] = []
    for server_id, name, url, registry_source, risk_tier, last_assessed in registry:
        bucket = newest.get(server_id, {})
        overall = bucket.get("overall_risk") or {}
        maintainer = bucket.get("maintainer_trust") or {}
        rows.append({
            "server_id": server_id,
            "name": name,
            "url": url,
            "registry_source": registry_source,
            "risk_tier": risk_tier,
            "last_assessed": last_assessed,
            "last_scored_at": overall.get("scored_at"),
            "overall_risk": overall.get("label"),
            "maintainer_trust": maintainer.get("label"),
        })
    return rows


@contextmanager
def _session_scope():
    """One real app session (read-only use), closed on exit."""
    provider = get_session()
    session = next(provider)
    try:
        yield session
    finally:
        session.close()


def _resolve_rows(conn_or_rows: Any = None) -> List[Dict[str, Any]]:
    """Accept an explicit row list, an app session, or nothing (open our own)."""
    if conn_or_rows is None:
        try:
            with _session_scope() as session:
                return fetch_rows(session)
        except Exception:
            return []
    if isinstance(conn_or_rows, dict):
        return [conn_or_rows]
    if isinstance(conn_or_rows, (list, tuple, set)):
        return list(conn_or_rows)
    if hasattr(conn_or_rows, "execute"):
        return fetch_rows(conn_or_rows)
    return list(conn_or_rows)


# ---------------------------------------------------------------- pure core


def _published_tier(row: Any) -> str:
    """Registry risk tier, capped for verified publishers via trust_gate."""
    tier = _row_get(row, "risk_tier") or "-"
    labels = {}
    for axis in _AXES_OF_INTEREST:
        label = _row_get(row, axis)
        if label:
            labels[axis] = label
    gate = trust_gate(_row_get(row, "url"), _row_get(row, "name"), labels)
    published = (gate.get("published_overall_risk") or "").upper()
    if gate.get("capped") and published and str(tier).upper() in ("HIGH", "CRITICAL"):
        return published
    return tier


def _entries(rows: Iterable[Any]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    now = _now()
    for row in rows or []:
        last_scored = _parse_dt(_row_get(row, "last_scored_at")
                                or _row_get(row, "scored_at"))
        age_hours = None
        if last_scored is not None:
            age_hours = (now - last_scored).total_seconds() / 3600.0
            if age_hours < 0:
                age_hours = 0.0
        if age_hours is None:
            state = "missing"
        elif age_hours > VERY_STALE_HOURS:
            state = "very_stale"
        elif age_hours > STALE_HOURS:
            state = "stale"
        else:
            state = "fresh"
        out.append({
            "server_id": _row_get(row, "server_id"),
            "name": _row_get(row, "name"),
            "registry_source": _row_get(row, "registry_source"),
            "risk_tier": _published_tier(row),
            "last_scored_at": last_scored,
            "age_hours": age_hours,
            "state": state,
        })
    # missing first, then oldest score first, then server_id for stability
    out.sort(key=lambda e: (0 if e["state"] == "missing" else 1,
                             -(e["age_hours"] if e["age_hours"] is not None else 1e12),
                             str(e["server_id"])))
    return out


def render_markdown(rows: Iterable[Any]) -> str:
    """Render the freshness ledger as Markdown."""
    entries = _entries(rows)
    total = len(entries)
    scored = sum(1 for e in entries if e["state"] != "missing")
    missing = total - scored
    stale_24 = sum(1 for e in entries if e["age_hours"] is not None
                   and e["age_hours"] > STALE_HOURS)
    stale_168 = sum(1 for e in entries if e["age_hours"] is not None
                    and e["age_hours"] > VERY_STALE_HOURS)

    lines = [
        "# Score Freshness Ledger",
        "",
        f"Generated at: {_now().isoformat()}",
        "",
        "| " + " | ".join(COLUMNS) + " |",
        "|" + "---|" * len(COLUMNS),
    ]
    for e in entries:
        age = "-" if e["age_hours"] is None else f"{e['age_hours']:.1f}"
        lines.append("| " + " | ".join([
            _cell(e["server_id"]),
            _cell(e["name"]),
            _cell(e["registry_source"]),
            _cell(e["risk_tier"]),
            _fmt_ts(e["last_scored_at"]),
            age,
            e["state"],
        ]) + " |")

    lines.extend([
        "",
        (f"Summary: total servers={total}, scored={scored}, missing={missing}, "
         f"stale>{STALE_HOURS}h={stale_24}, stale>{VERY_STALE_HOURS}h={stale_168}"),
    ])
    return "\n".join(lines)


def generate_report(conn_or_rows: Any = None) -> str:
    """Markdown freshness ledger. `conn_or_rows` may be a row list, an app
    session, or None (open a real app session)."""
    return render_markdown(_resolve_rows(conn_or_rows))


def write_report(path: str, conn_or_rows: Any = None) -> str:
    """Write the ledger to `path` and return the absolute path."""
    body = generate_report(conn_or_rows)
    target = os.path.abspath(path)
    parent = os.path.dirname(target)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(target, "w", encoding="utf-8") as fh:
        fh.write(body + "\n")
    return target


# ---------------------------------------------------------------- entrypoint


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Scoring freshness ledger (Markdown)")
    parser.add_argument("--out", default="", help="optional path for the Markdown report")
    args = parser.parse_args(argv)

    body = generate_report()
    if args.out:
        path = write_report(args.out)
        print(f"wrote {path}")
    else:
        print(body)
    return 0


if __name__ == "__main__":  # CI-safe self-test: real imports, SQLite session for the DB path
    import tempfile
    from datetime import timedelta

    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    from app.models import Base

    now = _now()

    # --- synthetic rows: scored 2h ago, scored 100h ago, never scored ---
    synth = [
        {"server_id": "srv_recent", "name": "Recent MCP",
         "registry_source": "official", "risk_tier": "LOW",
         "last_assessed": now - timedelta(hours=2),
         "last_scored_at": now - timedelta(hours=2)},
        {"server_id": "srv_old", "name": "Old MCP",
         "registry_source": "community", "risk_tier": "MEDIUM",
         "last_assessed": now - timedelta(hours=100),
         "last_scored_at": now - timedelta(hours=100)},
        {"server_id": "srv_none", "name": "Unscored MCP",
         "registry_source": "community", "risk_tier": None,
         "last_assessed": None, "last_scored_at": None},
    ]

    md = generate_report(synth)
    assert isinstance(md, str) and md, md
    assert md.startswith("# Score Freshness Ledger"), md
    assert "| server_id | name | registry_source | risk_tier | last_scored_at | age_hours | state |" in md, md
    assert "missing=1" in md, md
    assert "total servers=3" in md, md
    for sid in ("srv_recent", "srv_old", "srv_none"):
        assert sid in md, md
    assert "missing" in md and "stale" in md, md
    assert md.index("srv_none") < md.index("srv_old") < md.index("srv_recent"), md
    assert "stale>24h=1" in md, md
    assert "stale>168h=0" in md, md

    # --- empty state ---
    empty = generate_report([])
    assert isinstance(empty, str) and empty, empty
    assert "total servers=0" in empty and "missing=0" in empty, empty

    # --- write_report round-trip ---
    with tempfile.TemporaryDirectory() as tmp:
        target = os.path.join(tmp, "nested", "freshness.md")
        path = write_report(target, synth)
        assert path == os.path.abspath(target), path
        assert os.path.isfile(path), path
        with open(path, encoding="utf-8") as fh:
            written = fh.read()
        strip = lambda t: "\n".join(
            ln for ln in t.rstrip("\n").splitlines()
            if not ln.startswith("Generated at:"))
        assert strip(written) == strip(md), written

    # --- real data layer path (SQLite session over the real models) ---
    eng = create_engine("sqlite://", connect_args={"check_same_thread": False},
                        poolclass=StaticPool)
    Base.metadata.create_all(eng)
    TS = sessionmaker(bind=eng, autoflush=False, autocommit=False)
    s = TS()
    s.add(McpServerRegistry(server_id="srv1", name="Stripe MCP",
                            url="https://github.com/stripe/agent-toolkit",
                            registry_source="official", risk_tier="HIGH"))
    s.add(McpServerRegistry(server_id="srv2", name="Community MCP",
                            url="https://example.invalid/mcp",
                            registry_source="community", risk_tier="LOW"))
    s.add(McpLlmAxisScore(id=1, server_id="srv1", axis_name="overall_risk",
                          label="HIGH", model_version="v3.0_40974559",
                          scored_at=now - timedelta(hours=3)))
    s.add(McpLlmAxisScore(id=2, server_id="srv1", axis_name="maintainer_trust",
                          label="ESTABLISHED", model_version="v3.0_40974559"))
    s.add(McpLlmAxisScore(id=3, server_id="srv2", axis_name="overall_risk",
                          label="LOW", model_version="v3.0_40974559",
                          scored_at=now - timedelta(hours=300)))
    s.commit()

    live = generate_report(s)
    assert isinstance(live, str) and live, live
    assert "srv1" in live and "srv2" in live, live
    assert "missing=0" in live, live
    assert "stale>24h=1" in live, live
    assert "stale>168h=1" in live, live
    assert "very_stale" in live, live
    s.close()

    print("PASS")
