"""FU-076 (step 1): report-only distribution of the DETERMINISTIC, legible
per-server signals we can compute TODAY from the app-tier registry -- printed
next to the risk_tier distribution to quantify the FU-058 collapse (almost the
whole corpus wears HIGH/CRITICAL, so the tier carries little information) and to
show that legible deterministic signals actually spread.

REPORT ONLY. This module reads the registry and returns/prints distributions. It
changes no scores, re-tiers nothing, mounts no route, writes no registry data.

Signals computed here (all deterministic, all available today): transport (from
url), public-repo presence (from url), has_known_cve (membership in the vuln_links
join -- the same deterministic linkage the has_known_cve facet uses), OUR scan
recency (last_scanned), and which community-signal keys exist in the free-form
`meta` blob.

Scope honesty (FU-076): none of the FOUR marquee signals that follow-up named are
computable from the app tier yet. Repo
maintenance-age, declared scopes/permissions, tests-present and pinned-deps are
NOT in the app Postgres tier; they live only in the builder DuckDB and need
ingestion first. That gap is recorded in SIGNALS_NEEDING_INGESTION below (its own
follow-up), not papered over.

Deterministic == no LLM, no network: every value is a pure function of columns
already stored, so the same input yields the same number on every run.
"""
from __future__ import annotations

import json
import os
import re
from collections import Counter
from datetime import datetime, timezone, timedelta
from typing import Optional

# Legible signals the follow-up wanted that are NOT computable in the app tier
# today -- documented so the report names the ingestion gap instead of faking it.
SIGNALS_NEEDING_INGESTION = {
    "maintenance_age": "repo last-commit recency; lives in builder DuckDB "
                       "github_velocity(commit_velocity,last_suspicious_commit,checked_at); "
                       "needs a repo_pushed_at/age field mirrored onto mcp_server_registry",
    "scoped_permissions": "declared scopes / tool list; builder DuckDB "
                          "mcp_fingerprints.permission_scope_hash + "
                          "mcp_definition_history.snapshot_content; needs a "
                          "declared_scopes/tools column",
    "tests_present": "not stored anywhere; needs a repo/CI scan landing a has_tests boolean",
    "pinned_dependencies": "manifest pin-status; builder DuckDB "
                           "mcp_registry_facts.raw_packages; needs the manifest text "
                           "to derive pinned-vs-range",
}

# Deterministic public-repo host match (heuristic on the stored url, not the vuln
# stack's repo_key -- this is a report, not a gate, so a light inline check is fine).
_REPO_HOST_RE = re.compile(
    r"^https?://(www\.)?(github\.com|gitlab\.com|bitbucket\.org)/[^/]+/[^/]+", re.I)

_META_KEYS = ("age_days", "stars", "forks", "download_count",
              "dependency_count", "publisher_verified")

# Risk tiers treated as "elevated" for the FU-058 concentration metric.
_ELEVATED_TIERS = {"HIGH", "CRITICAL"}


def _transport(url: Optional[str]) -> str:
    if not url:
        return "none"
    u = url.strip().lower()
    if u.startswith("https://"):
        return "https"
    if u.startswith("http://"):
        return "http"
    return "other"


def _has_public_repo(url: Optional[str]) -> bool:
    return bool(url and _REPO_HOST_RE.match(url.strip()))


def _scan_bucket(last_scanned: Optional[datetime], now: datetime) -> str:
    if last_scanned is None:
        return "never"
    ts = last_scanned
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    days = (now - ts).total_seconds() / 86400.0
    if days <= 7:
        return "<=7d"
    if days <= 30:
        return "8-30d"
    if days <= 90:
        return "31-90d"
    return ">90d"


def _meta_keys_present(meta: Optional[str]) -> set:
    if not meta:
        return set()
    try:
        d = json.loads(meta)
    except Exception:
        return set()
    if not isinstance(d, dict):
        return set()
    return {k for k in _META_KEYS if d.get(k) is not None}


def compute_distributions(session, now: Optional[datetime] = None) -> dict:
    """Pure read over mcp_server_registry. Returns:
        {total, signals:{name:{value:count}}, meta_coverage:{key:count}}
    Never mutates. Never raises on empty."""
    from app.models import McpServerRegistry
    now = now or datetime.now(timezone.utc)
    rows = session.query(McpServerRegistry).all()

    # has_known_cve: deterministic membership in the vuln_links table (exact
    # advisory<->server identity match written by the vuln linker -- the same data
    # the has_known_cve facet exposes). No LLM, no ingestion; already computed.
    try:
        from app.models import VulnLink
        cve_ids = {sid for (sid,) in session.query(VulnLink.server_id).distinct()}
    except Exception:
        cve_ids = None  # vuln_links absent this run -> omit the axis, never raise

    tier, verdict = Counter(), Counter()
    transport, repo, scan = Counter(), Counter(), Counter()
    cve, meta_cov = Counter(), Counter()

    for r in rows:
        tier[(r.risk_tier or "UNSET").upper()] += 1
        verdict[r.verdict or "unset"] += 1
        transport[_transport(r.url)] += 1
        repo["true" if _has_public_repo(r.url) else "false"] += 1
        scan[_scan_bucket(r.last_scanned, now)] += 1
        if cve_ids is not None:
            cve["true" if r.server_id in cve_ids else "false"] += 1
        for k in _meta_keys_present(r.meta):
            meta_cov[k] += 1

    signals = {
        "risk_tier": dict(tier),        # blended LLM label -- the FU-058 collapse
        "verdict": dict(verdict),       # blended LLM label
        "transport": dict(transport),   # deterministic, from url
        "has_public_repo": dict(repo),  # deterministic, from url
        "has_known_cve": dict(cve),     # deterministic security axis, from vuln_links
        "scan_recency": dict(scan),     # OUR scan cadence, NOT upstream maintenance
    }
    if cve_ids is None:                 # table absent -> signal genuinely unavailable
        del signals["has_known_cve"]

    return {
        "total": len(rows),
        "signals": signals,
        "meta_coverage": dict(meta_cov),
    }


def summarize(dist: dict) -> dict:
    """Per-signal spread proxy + the targeted FU-058 metric. Report-only.
        max_share: fraction in the single most common value (1.0 == fully
                   collapsed / uninformative; lower == spreads / informative).
        risk_tier additionally carries elevated_share = share in HIGH|CRITICAL,
        which is the exact FU-058 number.
    """
    total = dist["total"] or 1
    out = {}
    for name, counts in dist["signals"].items():
        top = max(counts.values()) if counts else 0
        out[name] = {"max_share": round(top / total, 4),
                     "distinct_values": len(counts)}
    tier = dist["signals"].get("risk_tier", {})
    elevated = sum(n for v, n in tier.items() if v in _ELEVATED_TIERS)
    out["risk_tier"]["elevated_share"] = round(elevated / total, 4)
    return out


def render_text(dist: dict) -> str:
    summ = summarize(dist)
    total = dist["total"] or 1
    lines = [f"deterministic signal distribution over {dist['total']} servers", ""]
    for name, counts in dist["signals"].items():
        s = summ[name]
        tag = f"max_share={s['max_share']}"
        if name == "risk_tier":
            tag += f" elevated(HIGH|CRITICAL)_share={s['elevated_share']}"
        lines.append(f"{name}  ({tag}, distinct={s['distinct_values']})")
        for v, n in sorted(counts.items(), key=lambda kv: -kv[1]):
            lines.append(f"    {str(v):<14} {n:>9}  {n/total:6.1%}")
        lines.append("")
    lines.append("community-signal coverage in free-form meta (uncontracted keys):")
    for k in _META_KEYS:
        lines.append(f"    {k:<18} present on {dist['meta_coverage'].get(k, 0)}/{dist['total']}")
    lines.append("")
    lines.append("legible signals NOT computable in the app tier yet (need ingestion):")
    for k, why in SIGNALS_NEEDING_INGESTION.items():
        lines.append(f"    {k}: {why}")
    return "\n".join(lines)


def to_json(dist: dict) -> str:
    return json.dumps({"distributions": dist, "summary": summarize(dist)},
                      indent=2, sort_keys=True)


def _selftest() -> None:
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.db import Base
    from app.models import McpServerRegistry, VulnLink
    eng = create_engine("sqlite://")
    Base.metadata.create_all(eng)
    s = sessionmaker(bind=eng)()
    now = datetime(2026, 7, 22, tzinfo=timezone.utc)

    rows = []
    # 17 HIGH + 2 CRITICAL + 1 LOW == the FU-058 collapse: 19/20 elevated.
    for i in range(17):
        rows.append(McpServerRegistry(
            server_id=f"h{i}", risk_tier="HIGH", verdict="risky",
            url=f"https://github.com/o/r{i}", last_scanned=now - timedelta(days=2)))
    for i in range(2):
        rows.append(McpServerRegistry(
            server_id=f"c{i}", risk_tier="CRITICAL", verdict="risky",
            url="http://mcp.example.io/x", last_scanned=now - timedelta(days=200),
            meta=json.dumps({"stars": 3, "age_days": 40})))
    rows.append(McpServerRegistry(
        server_id="l0", risk_tier="LOW", verdict="safe",
        url="https://gitlab.com/o/clean", last_scanned=None,
        meta=json.dumps({"download_count": 12})))
    s.add_all(rows)
    s.commit()
    # two servers carry a known CVE (deterministic vuln_links membership).
    s.add_all([
        VulnLink(advisory_id="GHSA-1", server_id="h0", match_basis="repo_exact",
                 match_value="repo:github.com/o/r0", match_confidence=1.0),
        VulnLink(advisory_id="GHSA-2", server_id="c0", match_basis="repo_exact",
                 match_value="repo:mcp.example.io", match_confidence=1.0),
    ])
    s.commit()

    dist = compute_distributions(s, now=now)
    summ = summarize(dist)

    assert dist["total"] == 20, dist["total"]
    # risk_tier is collapsed / uninformative: 19 of 20 elevated.
    assert summ["risk_tier"]["elevated_share"] == 0.95, summ["risk_tier"]
    assert dist["signals"]["risk_tier"]["HIGH"] == 17
    # deterministic signals PARTITION the corpus (>=2 distinct values, each with a
    # real minority) -- the report measures each signal's spread; it does not assume
    # a signal spreads. Here transport carves out a genuine http minority.
    assert dist["signals"]["transport"]["https"] == 18
    assert dist["signals"]["transport"]["http"] == 2
    assert summ["transport"]["distinct_values"] >= 2
    # public-repo presence is a clean boolean partition.
    assert dist["signals"]["has_public_repo"]["true"] == 18   # github+gitlab
    assert dist["signals"]["has_public_repo"]["false"] == 2   # the example.io pair
    # has_known_cve: deterministic security axis from vuln_links -- 2 linked, 18 not.
    assert dist["signals"]["has_known_cve"] == {"true": 2, "false": 18}, dist["signals"].get("has_known_cve")
    # scan recency spreads across buckets incl. never.
    assert dist["signals"]["scan_recency"]["never"] == 1
    assert dist["signals"]["scan_recency"]["<=7d"] == 17
    # meta coverage picks up seeded keys only where present.
    assert dist["meta_coverage"].get("stars") == 2
    assert dist["meta_coverage"].get("download_count") == 1
    assert dist["meta_coverage"].get("forks", 0) == 0
    # the honest gap is carried, not hidden.
    assert set(SIGNALS_NEEDING_INGESTION) == {
        "maintenance_age", "scoped_permissions", "tests_present", "pinned_dependencies"}
    # render paths don't raise and round-trip through json.
    assert "risk_tier" in render_text(dist)
    json.loads(to_json(dist))

    print(render_text(dist))
    print("\nPASS")


if __name__ == "__main__":
    os.environ.setdefault("DATABASE_URL", "sqlite://")
    _selftest()
