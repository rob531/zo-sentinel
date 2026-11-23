# deps: sqlalchemy, fastapi
"""registry_trust_distribution_report.py -- registry trust/risk-tier distribution report.

Standalone report generator (plus a thin read-only router) summarising how the MCP
registry population is distributed across trust_score and risk_tier for the 3-tier
dashboard.

Pure helpers (stdlib only):
    build_trust_distribution_report(rows: list[dict]) -> dict
    render_trust_distribution_report(report: dict) -> str

Data access (the REAL app data layer):
    run_report(db: Session | None = None) -> dict   # reads mcp_server_registry

No DB writes, no network, no import-time side effects.
"""
from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone
from statistics import fmean, median
from typing import Any, Optional, Sequence

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry

router = APIRouter(prefix="/api", tags=["registry"])

STALE_ASSESSMENT_DAYS = 30
UNSCORED_TIER = "UNSCORED"
UNSCORED_VERDICT = "unscored"


class TrustDistributionReport(BaseModel):
    total: int
    scored: int
    unscored: int
    mean_trust_score: Optional[float] = None
    median_trust_score: Optional[float] = None
    p10: Optional[float] = None
    p90: Optional[float] = None
    tiers: list[dict] = []
    verdicts: list[dict] = []
    stale_count: int = 0
    generated_at: str


def _as_float(value: Any) -> Optional[float]:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) else None


def _parse_dt(value: Any) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    text = str(value).strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _percentile(values: Sequence[float], quantile: float) -> Optional[float]:
    """Linearly interpolated percentile of an already-sorted list (q in 0..1)."""
    if not values:
        return None
    if len(values) == 1:
        return float(values[0])
    position = (len(values) - 1) * quantile
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return float(values[lower])
    lower_weight = upper - position
    upper_weight = position - lower
    return float(values[lower] * lower_weight + values[upper] * upper_weight)


def _pct_rows(counts: dict[str, int], total: int) -> list[dict]:
    """[{key, count, pct}] sorted by count desc then key; pcts sum to exactly 100."""
    entries = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    out: list[dict] = []
    for key, count in entries:
        pct = (100.0 * count / total) if total else 0.0
        out.append({"key": key, "count": count, "pct": round(pct, 2)})
    if out and total:
        residual = round(100.0 - sum(item["pct"] for item in out), 2)
        biggest = max(range(len(out)), key=lambda i: out[i]["count"])
        out[biggest]["pct"] = round(out[biggest]["pct"] + residual, 2)
    return out


def build_trust_distribution_report(rows: list[dict]) -> dict:
    """Summarise the trust_score / risk_tier distribution of registry rows."""
    rows = list(rows or [])
    total = len(rows)
    now = datetime.now(timezone.utc)
    stale_cutoff = now - timedelta(days=STALE_ASSESSMENT_DAYS)

    scores: list[float] = []
    tier_counts: dict[str, int] = {}
    verdict_counts: dict[str, int] = {}
    stale_count = 0

    for row in rows:
        score = _as_float(row.get("trust_score"))
        if score is not None:
            scores.append(score)

        tier = str(row.get("risk_tier") or "").strip().upper() or UNSCORED_TIER
        tier_counts[tier] = tier_counts.get(tier, 0) + 1

        verdict = str(row.get("verdict") or "").strip().lower() or UNSCORED_VERDICT
        verdict_counts[verdict] = verdict_counts.get(verdict, 0) + 1

        assessed = _parse_dt(row.get("last_assessed"))
        if assessed is None or assessed < stale_cutoff:
            stale_count += 1

    scored = len(scores)
    unscored = total - scored
    ordered = sorted(scores)

    tiers = _pct_rows(tier_counts, total)
    verdicts = _pct_rows(verdict_counts, total)

    return {
        "total": total,
        "scored": scored,
        "unscored": unscored,
        "mean_trust_score": round(fmean(ordered), 4) if scored else None,
        "median_trust_score": round(float(median(ordered)), 4) if scored else None,
        "p10": round(_percentile(ordered, 0.10), 4) if scored else None,
        "p90": round(_percentile(ordered, 0.90), 4) if scored else None,
        "tiers": [{"risk_tier": item["key"], "count": item["count"], "pct": item["pct"]}
                  for item in tiers],
        "verdicts": [{"verdict": item["key"], "count": item["count"], "pct": item["pct"]}
                     for item in verdicts],
        "stale_count": stale_count,
        "generated_at": now.isoformat().replace("+00:00", "Z"),
    }


def _fmt(value: Any) -> str:
    if value is None:
        return "N/A"
    if isinstance(value, float):
        return format(value, ".4f")
    return str(value)


def render_trust_distribution_report(report: dict) -> str:
    """Plain-text rendering of the report dict."""
    report = report or {}
    lines = [
        "REGISTRY TRUST DISTRIBUTION",
        f"generated_at: {report.get('generated_at')}",
        f"total: {report.get('total', 0)}",
        f"scored: {report.get('scored', 0)}",
        f"unscored: {report.get('unscored', 0)}",
        f"mean_trust_score: {_fmt(report.get('mean_trust_score'))}",
        f"median_trust_score: {_fmt(report.get('median_trust_score'))}",
        f"p10: {_fmt(report.get('p10'))}",
        f"p90: {_fmt(report.get('p90'))}",
        f"stale_count: {report.get('stale_count', 0)}",
        "",
        f"{'RISK TIER':<24}{'COUNT':>8}{'PCT':>10}",
    ]
    for tier in report.get("tiers") or []:
        lines.append(f"{str(tier.get('risk_tier')):<24}"
                     f"{int(tier.get('count', 0)):>8}{float(tier.get('pct', 0.0)):>10.2f}")
    lines += ["", f"{'VERDICT':<24}{'COUNT':>8}{'PCT':>10}"]
    for verdict in report.get("verdicts") or []:
        lines.append(f"{str(verdict.get('verdict')):<24}"
                     f"{int(verdict.get('count', 0)):>8}{float(verdict.get('pct', 0.0)):>10.2f}")
    return "\n".join(lines)


def load_registry_rows(db: Session) -> list[dict]:
    """Read the registry rows the report needs from the app data layer."""
    rows = db.execute(select(McpServerRegistry)).scalars().all()
    return [
        {
            "server_id": row.server_id,
            "name": row.name,
            "trust_score": row.trust_score,
            "risk_tier": row.risk_tier,
            "verdict": row.verdict,
            "last_assessed": row.last_assessed,
        }
        for row in rows
    ]


def run_report(db: Optional[Session] = None) -> dict:
    """Build the report from the app DB (uses the app session when db is None)."""
    if db is not None:
        return build_trust_distribution_report(load_registry_rows(db))
    gen = get_session()
    session = next(gen)
    try:
        return build_trust_distribution_report(load_registry_rows(session))
    finally:
        try:
            next(gen)
        except StopIteration:
            pass


@router.get("/registry/trust-distribution", response_model=TrustDistributionReport)
def registry_trust_distribution(db: Session = Depends(get_session)) -> dict:
    """Registry trust_score / risk_tier distribution (read-only)."""
    return run_report(db)


def _self_test() -> None:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.models import Base

    now = datetime.now(timezone.utc)

    # --- pure-function fixture: three rows, two scored, one unscored ---
    fixtures = [
        {"server_id": "srv-a", "name": "Alpha MCP", "trust_score": 0.8,
         "risk_tier": "LOW", "verdict": "clean", "last_assessed": now},
        {"server_id": "srv-b", "name": "Beta MCP", "trust_score": 0.2,
         "risk_tier": "HIGH", "verdict": "flagged", "last_assessed": now},
        {"server_id": "srv-c", "name": "Gamma MCP", "trust_score": None,
         "risk_tier": None, "verdict": None, "last_assessed": None},
    ]
    report = build_trust_distribution_report(fixtures)
    assert report["total"] == 3, report
    assert report["scored"] == 2, report
    assert report["unscored"] == 1, report
    assert abs(report["mean_trust_score"] - (0.8 + 0.2) / 2) < 1e-9, report
    assert abs(report["median_trust_score"] - 0.5) < 1e-9, report
    assert abs(sum(t["pct"] for t in report["tiers"]) - 100.0) <= 0.01, report
    assert abs(sum(v["pct"] for v in report["verdicts"]) - 100.0) <= 0.01, report
    assert report["stale_count"] == 1, report
    assert report["generated_at"], report

    text = render_trust_distribution_report(report)
    assert "RISK TIER" in text and "srv" not in text, text
    assert str(report["total"]) in text, text

    # --- edge case: no rows ---
    empty = build_trust_distribution_report([])
    assert empty["total"] == 0 and empty["scored"] == 0 and empty["unscored"] == 0, empty
    assert empty["mean_trust_score"] is None and empty["p90"] is None, empty
    assert render_trust_distribution_report(empty)

    # --- real app data layer (SQLite via dependency override) ---
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                            poolclass=StaticPool)
    Base.metadata.create_all(engine)
    TS = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    session = TS()
    session.add(McpServerRegistry(server_id="srv1", name="Alpha MCP",
                                  url="https://github.com/acme/alpha",
                                  registry_source="github", trust_score=0.75,
                                  risk_tier="TRUSTED_GENERAL", verdict="LOW",
                                  last_assessed=now))
    session.add(McpServerRegistry(server_id="srv2", name="Beta MCP",
                                  url="https://github.com/acme/beta",
                                  registry_source="github", trust_score=0.25,
                                  risk_tier="HIGH_RISK_ISOLATED", verdict="HIGH",
                                  last_assessed=now))
    session.add(McpServerRegistry(server_id="srv3", name="Gamma MCP",
                                  url="https://github.com/acme/gamma",
                                  registry_source="npm", risk_tier="INSUFFICIENT",
                                  verdict="unreviewed"))
    session.commit()
    session.close()

    app = FastAPI()
    app.include_router(router)

    def _override_session():
        db = TS()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_session] = _override_session
    client = TestClient(app)
    resp = client.get("/api/registry/trust-distribution")
    assert resp.status_code == 200, resp.text
    payload = resp.json()
    assert payload["total"] == 3, payload
    assert payload["scored"] == 2, payload
    assert payload["unscored"] == 1, payload
    assert abs(payload["mean_trust_score"] - 0.5) < 1e-6, payload
    assert abs(sum(t["pct"] for t in payload["tiers"]) - 100.0) <= 0.01, payload
    assert payload["stale_count"] == 1, payload

    db_session = TS()
    try:
        direct = run_report(db_session)
        assert direct["total"] == 3, direct
        assert direct["scored"] == 2, direct
    finally:
        db_session.close()


if __name__ == "__main__":
    _self_test()
    print("PASS")
