"""otx_threat_refs.py -- AlienVault OTX threat-intel references (context layer).

Records WHICH OTX pulses reference (a) the CVE aliases of advisories already
deterministically linked to registry servers, and (b) the non-git hosting
domains of registry servers. OTX is a CONTEXT dimension layered on top of
exact vuln links -- NEVER a linkage source itself (THE LINE stands: linkage is
vuln_identity exact-match only).

Precision-first by construction: queries are driven FROM OUR OWN KEYS
(per-CVE / per-domain indicator endpoints), never free-text search -- a bare
"MCP" OTX search sweeps ~8K mostly-inapplicable results (chairman-verified
2026-07-03). Aggregator roundup pulses (bulk Known_Cve-style feeds) are
recorded but flagged is_aggregator=True so downstream surfaces can separate
"appears in a bulk CVE roundup" from a curated exploitation report.

Provenance is FIRST-CLASS: every row carries pulse_id + pulse_name +
pulse_created + fetched_at; source_url is reconstructable from pulse_id.
Kill-switch: policy vuln.otx_enabled (default OFF; the read surface returns
disabled and ingestion refuses to run unless armed or forced).

The network fetch is an INJECTED seam (fetch_indicator callable) so
normalize+upsert is hermetically testable and CI never hits the network.
RealOtxFetcher (live runs only) resolves the key from OTX_API_KEY env or the
tower AgentVault convention. Idempotent: unique (indicator_type,
indicator_value, pulse_id). Bounded: per-axis limits + polite pacing.

Run:  python3 otx_threat_refs.py --live --cves --domains --limit 300
API:  POST /api/vuln/otx_refresh  (admin-only)  /  GET /api/vuln/threat_refs
"""
from __future__ import annotations

import json
import re
import time
from datetime import datetime, timezone
from typing import Callable, Optional

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import McpServerRegistry, ThreatIntelRef, VulnAdvisory, VulnLink
from verdict_breakdown_api import Principal, require_admin

router = APIRouter(prefix="/api", tags=["vuln"])

OTX_BASE = "https://otx.alienvault.com/api/v1"
OTX_PULSE_URL = "https://otx.alienvault.com/pulse/"
_AGGREGATOR_NAME_RX = re.compile(r"known[_ ]?cve|cve[_ ]?(roundup|collection|list)",
                                 re.IGNORECASE)

# fetch_indicator(indicator_type, value) -> parsed OTX /general dict or None.
FetchIndicator = Callable[[str, str], Optional[dict]]


def _now() -> datetime:
    return datetime.now(timezone.utc)


def kill_switch_on() -> bool:
    from zo_sentinel import policy
    return policy.flag("vuln.otx_enabled")


def is_aggregator_pulse(pulse: dict) -> bool:
    """Bulk CVE-roundup detection: name pattern or indicator flood. Flagged,
    not dropped -- provenance stays, downstream weighting differs."""
    if _AGGREGATOR_NAME_RX.search(pulse.get("name") or ""):
        return True
    if (pulse.get("indicator_count") or 0) >= 1000:
        return True
    return False


def normalize_pulses(indicator_type: str, value: str,
                     general: dict) -> list:
    """OTX /general response -> normalized ref dicts. Un-normalizable pulses
    (no id) are SKIPPED, never guessed."""
    out = []
    for p in ((general.get("pulse_info") or {}).get("pulses") or []):
        pid = p.get("id")
        if not pid:
            continue
        out.append({"indicator_type": indicator_type,
                    "indicator_value": value,
                    "pulse_id": str(pid),
                    "pulse_name": (p.get("name") or "")[:512],
                    "pulse_created": p.get("created"),
                    "is_aggregator": is_aggregator_pulse(p),
                    "source_url": OTX_PULSE_URL + str(pid)})
    return out


def _parse_dt(s: Optional[str]) -> Optional[datetime]:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except Exception:
        return None


def _upsert(db: Session, refs: list, existing: set) -> int:
    created = 0
    for r in refs:
        key = (r["indicator_type"], r["indicator_value"], r["pulse_id"])
        if key in existing:
            continue
        db.add(ThreatIntelRef(
            indicator_type=r["indicator_type"],
            indicator_value=r["indicator_value"],
            pulse_id=r["pulse_id"], pulse_name=r["pulse_name"],
            pulse_created=_parse_dt(r.get("pulse_created")),
            is_aggregator=r["is_aggregator"], source="otx",
            source_url=r["source_url"], fetched_at=_now()))
        existing.add(key)
        created += 1
    return created


def linked_cves(db: Session) -> list:
    """Distinct CVE aliases of advisories that have at least one exact
    registry link -- OUR keys, the precision driver."""
    adv_ids = {l.advisory_id for l in db.execute(select(VulnLink)).scalars()}
    cves = set()
    for adv in db.execute(select(VulnAdvisory)).scalars():
        if adv.id not in adv_ids:
            continue
        aliases = adv.aliases if isinstance(adv.aliases, list) else []
        for a in list(aliases) + [adv.id]:
            if isinstance(a, str) and a.upper().startswith("CVE-"):
                cves.add(a.upper())
    return sorted(cves)


def hosting_domains(db: Session) -> list:
    """Distinct non-git hosting domains, HIGH/CRITICAL-weighted first --
    the remote-hosted MCP attack surface."""
    weight = {}
    for srv in db.execute(select(McpServerRegistry)).scalars():
        u = (srv.url or "").lower()
        h = re.sub(r"^https?://(www\.)?", "", u).split("/")[0].split(":")[0]
        if (not h or h in ("github.com", "gitlab.com", "bitbucket.org")
                or not re.match(r"^[a-z0-9.-]+\.[a-z]{2,}$", h)):
            continue
        crit = 1 if (srv.risk_tier or "") in ("CRITICAL", "HIGH") else 0
        c, n = weight.get(h, (0, 0))
        weight[h] = (c + crit, n + 1)
    return [d for d, _ in
            sorted(weight.items(), key=lambda kv: (-kv[1][0], -kv[1][1]))]


def refresh(db: Session, fetch_indicator: FetchIndicator,
            do_cves: bool = True, do_domains: bool = True,
            limit: int = 300, force: bool = False) -> dict:
    """Pull OTX refs for our keys. Refuses when the kill-switch is off unless
    force=True (bootstrap ingestion before the surface is armed)."""
    if not kill_switch_on() and not force:
        return {"enabled": False, "note": "vuln.otx_enabled is off; "
                "pass force to ingest while the surface stays dark"}
    stats = {"cves_queried": 0, "domains_queried": 0,
             "refs_created": 0, "errors": 0}
    existing = {(t.indicator_type, t.indicator_value, t.pulse_id)
                for t in db.execute(select(ThreatIntelRef)).scalars()}
    work = []
    if do_cves:
        work += [("cve", v) for v in linked_cves(db)[:limit]]
    if do_domains:
        work += [("domain", v) for v in hosting_domains(db)[:limit]]
    for itype, value in work:
        d = fetch_indicator(itype, value)
        if d is None:
            stats["errors"] += 1
            continue
        stats["cves_queried" if itype == "cve" else "domains_queried"] += 1
        stats["refs_created"] += _upsert(db, normalize_pulses(itype, value, d),
                                         existing)
    db.commit()
    return stats


class RealOtxFetcher:
    """Live fetcher (constructed only for --live / API runs). Key from
    OTX_API_KEY env, else the tower AgentVault convention. Polite pacing."""

    def __init__(self, api_key: Optional[str] = None, sleep_s: float = 0.7,
                 timeout: int = 30):
        import os
        self.key = api_key or os.environ.get("OTX_API_KEY") or self._vault()
        self.sleep_s, self.timeout = sleep_s, timeout
        if not self.key:
            raise RuntimeError("no OTX key: set OTX_API_KEY or AgentVault 'alienvault'")

    @staticmethod
    def _vault() -> Optional[str]:
        import subprocess, sys
        try:
            return subprocess.check_output(
                [sys.executable, r"D:\agentvault\fetch_secret.py", "alienvault"],
                text=True, timeout=30).strip() or None
        except Exception:
            return None

    def __call__(self, indicator_type: str, value: str) -> Optional[dict]:
        import urllib.request
        path = {"cve": f"/indicators/cve/{value}/general",
                "domain": f"/indicators/domain/{value}/general"}.get(indicator_type)
        if not path:
            return None
        try:
            req = urllib.request.Request(
                OTX_BASE + path, headers={"X-OTX-API-KEY": self.key})
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                out = json.load(r)
            time.sleep(self.sleep_s)
            return out
        except Exception:
            return None


@router.post("/vuln/otx_refresh")
def api_refresh(limit: int = 300, force: bool = False,
                db: Session = Depends(get_session),
                principal: Principal = Depends(require_admin)) -> dict:
    return refresh(db, RealOtxFetcher(), limit=min(limit, 1000), force=force)


@router.get("/vuln/threat_refs")
def api_threat_refs(indicator_value: Optional[str] = None,
                    db: Session = Depends(get_session),
                    principal: Principal = Depends(require_admin)) -> dict:
    """Read surface. Kill-switch off => disabled + NO claims (caller renders
    INSUFFICIENT, mirroring vuln_exposure_api)."""
    if not kill_switch_on():
        return {"enabled": False, "refs": []}
    q = select(ThreatIntelRef)
    if indicator_value:
        q = q.where(ThreatIntelRef.indicator_value == indicator_value)
    refs = [{"indicator_type": t.indicator_type,
             "indicator_value": t.indicator_value,
             "pulse_id": t.pulse_id, "pulse_name": t.pulse_name,
             "pulse_created": t.pulse_created.isoformat() if t.pulse_created else None,
             "is_aggregator": t.is_aggregator,
             "source_url": t.source_url}
            for t in db.execute(q.limit(500)).scalars()]
    return {"enabled": True, "refs": refs}


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--live", action="store_true")
    ap.add_argument("--cves", action="store_true")
    ap.add_argument("--domains", action="store_true")
    ap.add_argument("--limit", type=int, default=300)
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()
    if args.live:
        from app.db import SessionLocal
        with SessionLocal() as s:
            print(json.dumps(refresh(
                s, RealOtxFetcher(), do_cves=args.cves or not args.domains,
                do_domains=args.domains or not args.cves,
                limit=args.limit, force=args.force)))
    else:
        # hermetic self-test
        g = {"pulse_info": {"count": 2, "pulses": [
            {"id": "p1", "name": "Actor X exploits CVE-2026-1", "indicator_count": 12},
            {"id": "p2", "name": "Known_Cve | Mar 31 | Part 2/2", "indicator_count": 4000},
            {"name": "no id -> skipped"}]}}
        refs = normalize_pulses("cve", "CVE-2026-1", g)
        assert len(refs) == 2
        assert refs[0]["is_aggregator"] is False
        assert refs[1]["is_aggregator"] is True          # name + flood
        assert refs[0]["source_url"] == OTX_PULSE_URL + "p1"
        assert is_aggregator_pulse({"name": "x", "indicator_count": 999}) is False
        print("PASS")
