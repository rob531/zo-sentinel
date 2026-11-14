"""org_usage_rollup_report.py -- org usage / tenancy rollup (non-HTTP report).

Reads the app tenancy tables (orgs, users, api_keys) plus the MCP registry
through the REAL app data layer (app.db session helper + app.models) -- no
inline stubs, no writes, no deletes, no network, no duckdb.

Public surface:
    generate_org_usage_report(org_id: str | None = None, db: Session | None = None) -> dict
    report_to_markdown(report: dict) -> str
    main() -> prints the JSON report
"""
from __future__ import annotations

import json
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Iterator, Optional

from sqlalchemy import func, inspect, select, text
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import ApiKey, McpServerRegistry, Org, User
from trust_gating_override import trust_gate


@contextmanager
def _session_ctx(db: Optional[Session] = None) -> Iterator[Session]:
    """Use a caller-supplied session, else one from the app session helper."""
    if db is not None:
        yield db
        return
    gen = get_session()
    session = next(gen)
    try:
        yield session
    finally:
        gen.close()


def _iso(value) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.isoformat()
    return str(value)


def _registry_has_org_id(session: Session) -> bool:
    """True only when the live registry table really carries an org_id column."""
    try:
        cols = {c["name"] for c in inspect(session.get_bind()).get_columns("mcp_server_registry")}
    except Exception:
        return False
    return "org_id" in cols


def _servers_per_org(session: Session) -> dict:
    """org_id -> server count, only possible when the registry carries org_id."""
    rows = session.execute(text(
        'SELECT "org_id", COUNT(*) FROM mcp_server_registry '
        'WHERE "org_id" IS NOT NULL GROUP BY "org_id"')).all()
    return {r[0]: int(r[1]) for r in rows}


def _risk_tier_counts(session: Session) -> dict:
    """Published (trust-gated) risk tier histogram over the registry."""
    out: dict = {}
    rows = session.execute(select(McpServerRegistry.url, McpServerRegistry.name,
                                  McpServerRegistry.risk_tier)).all()
    for url, name, tier in rows:
        gate = trust_gate(url, name, {"overall_risk": (tier or "").upper()})
        published = (gate.get("published_overall_risk") or tier or "UNKNOWN").upper()
        out[published] = out.get(published, 0) + 1
    return dict(sorted(out.items()))


def generate_org_usage_report(org_id: Optional[str] = None,
                              db: Optional[Session] = None) -> dict:
    """Org usage / tenancy rollup. Read-only: never writes, never deletes."""
    with _session_ctx(db) as session:
        org_stmt = select(Org)
        if org_id:
            org_stmt = org_stmt.where(Org.id == org_id)
        orgs = session.execute(org_stmt.order_by(Org.id)).scalars().all()
        org_ids = [o.id for o in orgs]

        users_by_org: dict = {}
        keys_by_org: dict = {}
        if org_ids:
            users_by_org = dict(session.execute(
                select(User.org_id, func.count()).where(User.org_id.in_(org_ids))
                .group_by(User.org_id)).all())
            keys_by_org = dict(session.execute(
                select(ApiKey.org_id, func.count()).where(ApiKey.org_id.in_(org_ids))
                .group_by(ApiKey.org_id)).all())

        scoped = _registry_has_org_id(session)
        server_count = int(session.execute(
            select(func.count()).select_from(McpServerRegistry)).scalar() or 0)
        per_org_servers = _servers_per_org(session) if scoped else {}
        last_assessed = session.execute(
            select(func.max(McpServerRegistry.last_assessed))).scalar()
        risk_tiers = _risk_tier_counts(session)

        entries = []
        for o in orgs:
            entries.append({
                "org_id": o.id,
                "name": o.name,
                "created_at": _iso(o.created_at),
                "user_count": int(users_by_org.get(o.id, 0)),
                "api_key_count": int(keys_by_org.get(o.id, 0)),
                "server_count": int(per_org_servers.get(o.id, 0)) if scoped else 0,
                "last_assessed_at": _iso(last_assessed),
            })

        return {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "org_id": org_id,
            "orgs": entries,
            "totals": {
                "orgs": len(entries),
                "users": sum(e["user_count"] for e in entries),
                "api_keys": sum(e["api_key_count"] for e in entries),
            },
            "server_count": server_count,
            "server_count_scope": "per_org" if scoped else "global",
            "last_assessed_at": _iso(last_assessed),
            "risk_tiers": risk_tiers,
        }


def report_to_markdown(report: dict) -> str:
    """Render the report dict as markdown."""
    totals = report.get("totals") or {}
    lines = [
        "# Org usage rollup",
        "",
        f"- generated_at: {report.get('generated_at')}",
        f"- org_id: {report.get('org_id')}",
        f"- totals: orgs={totals.get('orgs', 0)} users={totals.get('users', 0)} "
        f"api_keys={totals.get('api_keys', 0)}",
        f"- servers ({report.get('server_count_scope', 'global')}): "
        f"{report.get('server_count', 0)}",
        "",
        "| org_id | name | users | api_keys | servers | last_assessed_at |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for o in report.get("orgs") or []:
        lines.append("| {org_id} | {name} | {user_count} | {api_key_count} | "
                     "{server_count} | {last_assessed_at} |".format(
                         org_id=o.get("org_id"),
                         name=o.get("name") or "",
                         user_count=o.get("user_count", 0),
                         api_key_count=o.get("api_key_count", 0),
                         server_count=o.get("server_count", 0),
                         last_assessed_at=o.get("last_assessed_at") or "-"))
    risk = report.get("risk_tiers") or {}
    if risk:
        lines.append("")
        lines.append("Published risk tiers: " + ", ".join(f"{k}={v}" for k, v in risk.items()))
    return "\n".join(lines) + "\n"


def main() -> None:
    report = generate_org_usage_report()
    print(json.dumps(report, indent=2, default=str))


if __name__ == "__main__":
    from datetime import datetime as _dt

    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.models import Base

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    Base.metadata.create_all(engine)
    TS = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    session = TS()
    session.add(Org(id="org-a", name="Alpha Corp", created_at=_dt(2025, 1, 1, 9, 0, 0)))
    session.add(Org(id="org-b", name="Beta Labs", created_at=_dt(2025, 2, 2, 9, 0, 0)))
    session.add(User(id="u1", email="a1@alpha.test", password_hash="x", org_id="org-a",
                     role="member", created_at=_dt(2025, 1, 3)))
    session.add(User(id="u2", email="a2@alpha.test", password_hash="x", org_id="org-a",
                     role="admin", created_at=_dt(2025, 1, 4)))
    session.add(User(id="u3", email="b1@beta.test", password_hash="x", org_id="org-b",
                     role="member", created_at=_dt(2025, 2, 3)))
    session.add(ApiKey(id="k1", org_id="org-a", key_hash="h1", label="ci",
                       created_at=_dt(2025, 1, 5)))
    session.add(ApiKey(id="k2", org_id="org-b", key_hash="h2", label="prod",
                       created_at=_dt(2025, 2, 5)))
    session.add(McpServerRegistry(server_id="srv1", name="Stripe MCP",
                                  url="https://github.com/stripe/agent-toolkit",
                                  risk_tier="HIGH",
                                  last_assessed=_dt(2025, 3, 1, 12, 0, 0)))
    session.commit()

    report = generate_org_usage_report(db=session)
    for key in ("generated_at", "orgs", "totals"):
        assert key in report, f"missing {key}"
    assert len(report["orgs"]) == 2, report
    counts = sorted(o["user_count"] for o in report["orgs"])
    assert counts == [1, 2], counts
    assert report["totals"] == {"orgs": 2, "users": 3, "api_keys": 2}, report["totals"]
    assert report["server_count"] == 1, report

    md = report_to_markdown(report)
    assert "org-a" in md and "org-b" in md, md

    scoped = generate_org_usage_report(org_id="org-a", db=session)
    assert len(scoped["orgs"]) == 1 and scoped["orgs"][0]["org_id"] == "org-a", scoped
    assert scoped["totals"]["users"] == 2, scoped
    assert scoped["totals"]["api_keys"] == 1, scoped

    session.close()
    print("PASS")
