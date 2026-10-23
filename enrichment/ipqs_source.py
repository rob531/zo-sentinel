#!/usr/bin/env python3
"""
IPQualityScore enrichment source for ZO-Sentinel.

STATUS: wired, gated, and currently BLOCKED on two independent counts. It will
arm itself the moment both clear; no code change required.

  1. Credits. The key in the `ipqs` secret is genuine -- a deliberately invalid
     key answers "Invalid or unauthorized key", this key answers "You have
     insufficient credits" on all three products (url, ip, email). Auth is fine;
     the balance is zero.

  2. Join key. The population it would grade resolves to four hostnames
     (github.com, www.npmjs.com, smithery.ai, example.com). IPQS would be billed
     3,189 times to learn four facts, and would land the same constant that
     otx_threat_intel already writes. See enrichment_preflight.py.

Fixing (1) alone still buys a constant, which is why the gate checks both.

WHERE IPQS ACTUALLY EARNS ITS KEEP
----------------------------------
A URL-reputation vendor needs a per-server network identity. The registry's
`url` column is a *listing page*, so it has none. Two populations do:

  * remote/hosted MCP endpoints -- servers reachable on their own domain rather
    than shipped as an npm/GitHub package. Distinct per server.
  * egress destinations -- the domains a server's code actually contacts,
    extracted during analysis. Distinct per server, and the honest input to the
    network_egress signal.

Point `--population-sql` at either and the gate arms on cardinality. Point it at
the registry URL column and it blocks, by design.

USAGE
-----
    python3 ipqs_source.py --check-credits
    python3 ipqs_source.py --preflight
    python3 ipqs_source.py --run            # refuses unless the gate arms
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.parse
from dataclasses import dataclass
from typing import Any

import requests

from enrichment_preflight import (
    HOST_KEY,
    REGISTRY_POPULATION,
    PreflightError,
    WRITE_SERVICE,
    preflight,
    query,
)

SIGNAL_NAME = "ipqs_url_risk"
SECRET_ENV = "ipqs"
API_BASE = "https://www.ipqualityscore.com/api/json"
# IPQS bills per lookup. Declared budget is the ceiling the gate enforces.
DEFAULT_MONTHLY_BUDGET = 5_000


class CreditsUnavailable(RuntimeError):
    pass


@dataclass
class CreditStatus:
    usable: bool
    credits: int
    reason: str

    def render(self) -> str:
        return f"ipqs credits: {'OK' if self.usable else 'UNUSABLE'} ({self.credits:,}) -- {self.reason}"


def _key() -> str:
    key = os.environ.get(SECRET_ENV, "").strip()
    if not key:
        raise CreditsUnavailable(
            f"secret '{SECRET_ENV}' is not set in the environment"
        )
    return key


def check_credits(timeout: float = 20.0) -> CreditStatus:
    """Ask IPQS for the account balance.

    IPQS answers an unknown key and an exhausted one with different messages, so
    the two are reported as different states rather than a single opaque failure.
    """
    try:
        resp = requests.get(f"{API_BASE}/account/{_key()}", timeout=timeout)
        body = resp.json()
    except CreditsUnavailable:
        raise
    except (requests.RequestException, ValueError) as exc:
        return CreditStatus(False, 0, f"IPQS unreachable or non-JSON: {exc}")

    if body.get("success"):
        credits = int(body.get("credits", 0) or 0)
        used = int(body.get("usage", 0) or 0)
        return CreditStatus(credits > 0, credits, f"{used:,} used this period")

    msg = str(body.get("message", "")).lower()
    if "invalid" in msg or "unauthorized" in msg:
        return CreditStatus(False, 0, "key is invalid or unauthorized -- not a credit problem")
    if "credit" in msg:
        return CreditStatus(False, 0, "key authenticates but the account balance is zero")
    return CreditStatus(False, 0, f"unexpected IPQS response: {body.get('message')}")


def lookup_url(target: str, timeout: float = 25.0) -> dict[str, Any]:
    """One metered IPQS URL-scanner lookup. Callers must deduplicate first."""
    encoded = urllib.parse.quote(target, safe="")
    resp = requests.get(f"{API_BASE}/url/{_key()}/{encoded}", timeout=timeout)
    body = resp.json()
    if not body.get("success"):
        raise CreditsUnavailable(f"IPQS lookup failed: {body.get('message')}")
    return body


def to_score(body: dict[str, Any]) -> tuple[float, dict[str, Any]]:
    """Map an IPQS response onto the 0-100 ZO-Sentinel scale (higher = safer).

    IPQS risk_score runs the other way (higher = riskier), so it is inverted.
    Categorical flags clamp the result rather than nudging it: a domain flagged
    for malware is not a 40, it is a floor-level verdict regardless of its
    numeric risk.
    """
    risk = float(body.get("risk_score", 0) or 0)
    score = max(0.0, 100.0 - risk)
    flags = {
        k: bool(body.get(k))
        for k in ("unsafe", "malware", "phishing", "spamming", "suspicious", "parking")
        if body.get(k) is not None
    }
    if flags.get("malware") or flags.get("phishing"):
        score = min(score, 5.0)
    elif flags.get("unsafe"):
        score = min(score, 20.0)

    evidence = {
        "source": "ipqs_url_scanner",
        "ipqs_risk_score": risk,
        "domain": body.get("domain"),
        "domain_age_days": (body.get("domain_age") or {}).get("human")
        if isinstance(body.get("domain_age"), dict) else body.get("domain_age"),
        "dns_valid": body.get("dns_valid"),
        "flags": flags,
    }
    return round(score, 2), evidence


def write_scores(rows: list[tuple[str, float, dict[str, Any]]], dry_run: bool = True) -> int:
    """Write one row per server for this pass.

    Deliberately not a blind append. url_safety carries 2.4M rows for 3,173
    servers -- an average of 778 identical rows each -- because its writer
    appends on every cycle. This deletes the current day's rows for this signal
    before inserting, so a re-run replaces rather than accumulates.
    """
    if dry_run:
        return 0
    payload = ", ".join(
        "('{}', '{}', {}, '{}', now())".format(
            sid.replace("'", "''"),
            SIGNAL_NAME,
            score,
            json.dumps(ev).replace("'", "''"),
        )
        for sid, score, ev in rows
    )
    stmts = [
        f"DELETE FROM mcp_signal_scores WHERE signal_name='{SIGNAL_NAME}' "
        "AND scored_at >= CAST(current_date AS TIMESTAMP WITH TIME ZONE)",
        "INSERT INTO mcp_signal_scores (server_id, signal_name, score, evidence, scored_at) "
        f"VALUES {payload}",
    ]
    for sql in stmts:
        resp = requests.post(f"{WRITE_SERVICE}/execute", json={"sql": sql, "wait": True}, timeout=60)
        resp.raise_for_status()
    return len(rows)


def run(population_sql: str, join_key: str, budget: int, dry_run: bool, limit: int | None) -> int:
    status = check_credits()
    print(status.render())

    try:
        verdict = preflight(
            source="ipqs",
            population_sql=population_sql,
            join_key_expr=join_key,
            signal_name=SIGNAL_NAME,
            cost_per_lookup=1,
            monthly_budget=budget,
            credits_available=status.credits,
        )
    except PreflightError as exc:
        print(f"preflight[ipqs]: BLOCKED (check could not be evaluated)\n  {exc}", file=sys.stderr)
        return 2

    print(verdict.render())
    if not verdict.armed:
        print(
            "\nRefusing to spend metered lookups. Blockers: "
            + ", ".join(c.name for c in verdict.blockers),
            file=sys.stderr,
        )
        return 1

    # Deduplicate: one lookup per distinct key, fanned back out to every server
    # that shares it. This is the difference between 4 lookups and 3,189.
    rows = query(f"SELECT server_id, url, {join_key} AS k FROM ({population_sql}) t")
    if limit:
        rows = rows[:limit]
    by_key: dict[str, list[str]] = {}
    for r in rows:
        if r.get("k"):
            by_key.setdefault(r["k"], []).append(r["server_id"])

    print(f"\n{len(rows)} servers -> {len(by_key)} distinct lookups")
    scored: list[tuple[str, float, dict[str, Any]]] = []
    for i, (key, server_ids) in enumerate(sorted(by_key.items()), 1):
        try:
            body = lookup_url(f"https://{key}")
        except CreditsUnavailable as exc:
            print(f"  aborted at lookup {i}/{len(by_key)}: {exc}", file=sys.stderr)
            return 1
        score, ev = to_score(body)
        ev["shared_by_servers"] = len(server_ids)
        print(f"  [{i}/{len(by_key)}] {key} -> {score} ({len(server_ids)} servers)")
        scored.extend((sid, score, ev) for sid in server_ids)
        time.sleep(0.2)

    written = write_scores(scored, dry_run=dry_run)
    print(f"\n{'DRY RUN, nothing written' if dry_run else f'wrote {written} rows'}")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--check-credits", action="store_true")
    ap.add_argument("--preflight", action="store_true")
    ap.add_argument("--run", action="store_true")
    ap.add_argument("--population-sql", default=REGISTRY_POPULATION)
    ap.add_argument("--join-key", default=HOST_KEY)
    ap.add_argument("--monthly-budget", type=int, default=DEFAULT_MONTHLY_BUDGET)
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--commit", action="store_true", help="actually write rows (default is dry run)")
    args = ap.parse_args(argv)

    try:
        if args.check_credits:
            status = check_credits()
            print(status.render())
            return 0 if status.usable else 1

        if args.preflight or args.run:
            if args.preflight and not args.run:
                status = check_credits()
                print(status.render())
                verdict = preflight(
                    source="ipqs",
                    population_sql=args.population_sql,
                    join_key_expr=args.join_key,
                    signal_name=SIGNAL_NAME,
                    monthly_budget=args.monthly_budget,
                    credits_available=status.credits,
                )
                print(verdict.render())
                return 0 if verdict.armed else 1
            return run(args.population_sql, args.join_key, args.monthly_budget,
                       dry_run=not args.commit, limit=args.limit)
    except CreditsUnavailable as exc:
        print(f"ipqs: {exc}", file=sys.stderr)
        return 2

    ap.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
