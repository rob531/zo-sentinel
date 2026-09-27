#!/usr/bin/env python3
"""
Enrichment preflight -- the door every external enrichment source must pass
before it is allowed to write rows into mcp_signal_scores.

WHY THIS EXISTS
---------------
Two enrichment sources are live today and neither carries information:

  otx_threat_intel : 26,017 rows / 1,483 servers, every row
                     {"source":"otx_api","host":"github.com","pulse_count":0,
                      "malware_count":0,"reason":"otx_clean"}
                     -- it graded the AGGREGATOR's domain, not the server.
  url_safety       : 2,467,146 rows / 3,173 servers, 4 distinct scores,
                     evidence {"checks": [], "base_score": 70.0}.

The registry they draw from holds 3,189 http URLs across FOUR distinct hosts
(github.com 1611, www.npmjs.com 1322, smithery.ai 254, example.com 2). A
reputation vendor keyed on that column can only ever return four answers. The
defect is the JOIN KEY, not the vendor -- so swapping in a new vendor (IPQS,
say) reproduces the same constant at a new price.

`url_safety_discrimination_audit.py` was built to catch exactly this and could
not: it selects `score_value` where `signal_type='url_safety'` (real columns are
`score` and `signal_name`) and reads `result['data']` (WriteService returns
`rows`). Every path returns distinct_scores=0, which reads as "no data" rather
than "catastrophic uniformity". It fails OPEN.

So this gate fails CLOSED. A check that cannot run is a BLOCK, never a pass.

USAGE
-----
    from enrichment_preflight import preflight
    v = preflight(source="ipqs", population_sql=..., join_key_expr=...)
    if not v.armed:
        raise SystemExit(v.render())

    python3 enrichment_preflight.py --self-test     # proves both poles
    python3 enrichment_preflight.py --audit url_safety
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from typing import Any

import requests

WRITE_SERVICE = "http://127.0.0.1:8772"
SIGNAL_TABLE = "mcp_signal_scores"

# --- thresholds -------------------------------------------------------------
# A source must be able to tell the population apart. These are the minimum
# terms on which a metered vendor is worth calling at all.
MIN_DISTINCT_KEYS = 25      # fewer distinct join-key values than this is a constant
MAX_TOP1_SHARE = 0.60       # one key covering more than this is an aggregator
MIN_KEY_COVERAGE = 0.50     # share of the population that resolves to any key


class PreflightError(RuntimeError):
    """Raised when a check cannot be evaluated. Never swallowed."""


# --- WriteService access ----------------------------------------------------
def query(sql: str, timeout: float = 30.0) -> list[dict[str, Any]]:
    """Run a read query. Raises rather than returning empty on any failure.

    The WriteService answers a good query with {"rows": [...], "count": N} and a
    bad one with {"detail": "<parser error>"} at HTTP 200. Treating that detail
    body as an empty result set is how the previous audit blinded itself, so a
    missing `rows` key is an error here, not an absence of data.
    """
    try:
        resp = requests.post(f"{WRITE_SERVICE}/query", json={"sql": sql}, timeout=timeout)
    except requests.RequestException as exc:
        raise PreflightError(f"WriteService unreachable: {exc}") from exc

    if resp.status_code != 200:
        raise PreflightError(f"WriteService HTTP {resp.status_code}: {resp.text[:300]}")

    try:
        body = resp.json()
    except ValueError as exc:
        raise PreflightError(f"WriteService returned non-JSON: {resp.text[:300]}") from exc

    if "detail" in body:
        raise PreflightError(f"SQL rejected: {body['detail']}")
    if "rows" not in body:
        raise PreflightError(
            f"WriteService response has no 'rows' key (got {sorted(body)}). "
            "Refusing to read this as an empty result."
        )
    return body["rows"]


def assert_columns(table: str, columns: list[str]) -> None:
    """Fail closed if a referenced column does not exist.

    Claude has repeatedly hallucinated DuckDB column names here (mcp_request_id,
    score_value, signal_type on mcp_signal_scores). A query naming a phantom
    column raises a parser error that an over-tolerant caller reads as 'no
    findings', so the existence check happens before the query, not after.
    """
    rows = query(
        "SELECT column_name FROM information_schema.columns "
        f"WHERE table_name='{table}'"
    )
    present = {r["column_name"] for r in rows}
    if not present:
        raise PreflightError(f"Table '{table}' has no columns / does not exist")
    missing = [c for c in columns if c not in present]
    if missing:
        raise PreflightError(
            f"Table '{table}' is missing referenced column(s) {missing}. "
            f"Present: {sorted(present)}"
        )


# --- verdict ----------------------------------------------------------------
@dataclass
class Check:
    name: str
    passed: bool
    detail: str
    evidence: dict[str, Any] = field(default_factory=dict)


@dataclass
class PreflightVerdict:
    source: str
    checks: list[Check]

    @property
    def armed(self) -> bool:
        return all(c.passed for c in self.checks)

    @property
    def blockers(self) -> list[Check]:
        return [c for c in self.checks if not c.passed]

    def to_evidence(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "verdict": "ARMED" if self.armed else "BLOCKED",
            "checks": [
                {"name": c.name, "passed": c.passed, "detail": c.detail, **c.evidence}
                for c in self.checks
            ],
        }

    def render(self) -> str:
        head = "ARMED" if self.armed else "BLOCKED"
        lines = [f"preflight[{self.source}]: {head}"]
        for c in self.checks:
            lines.append(f"  [{'PASS' if c.passed else 'FAIL'}] {c.name}: {c.detail}")
        return "\n".join(lines)


# --- the checks -------------------------------------------------------------
def check_discrimination(
    population_sql: str,
    join_key_expr: str,
    min_distinct: int = MIN_DISTINCT_KEYS,
    max_top1_share: float = MAX_TOP1_SHARE,
) -> list[Check]:
    """Can this source tell the population apart at all?

    Measures the cardinality of the value the vendor would actually be handed --
    not the number of servers. 3,189 servers behind 4 hostnames is 4 lookups of
    information bought 3,189 times.
    """
    rows = query(
        f"SELECT {join_key_expr} AS k, COUNT(*) AS n "
        f"FROM ({population_sql}) t "
        f"GROUP BY 1 ORDER BY n DESC"
    )
    total = sum(r["n"] for r in rows)
    if total == 0:
        return [Check("discrimination", False, "population is empty", {"total": 0})]

    resolved = [r for r in rows if r["k"] not in (None, "")]
    resolved_n = sum(r["n"] for r in resolved)
    distinct = len(resolved)
    top1 = resolved[0]["n"] / resolved_n if resolved else 0.0
    top1_key = resolved[0]["k"] if resolved else None
    coverage = resolved_n / total

    ev = {
        "population": total,
        "distinct_keys": distinct,
        "key_coverage": round(coverage, 4),
        "top1_key": top1_key,
        "top1_share": round(top1, 4),
        "top5": [{"key": r["k"], "n": r["n"]} for r in resolved[:5]],
        "lookups_saved_by_dedup": resolved_n - distinct,
    }

    return [
        Check(
            "key_coverage",
            coverage >= MIN_KEY_COVERAGE,
            f"{coverage:.1%} of the population resolves to a key "
            f"(floor {MIN_KEY_COVERAGE:.0%})",
            ev,
        ),
        Check(
            "key_cardinality",
            distinct >= min_distinct,
            f"{distinct} distinct keys across {resolved_n} rows "
            f"(floor {min_distinct}) -- a vendor keyed here can return at most "
            f"{distinct} answers",
        ),
        Check(
            "key_concentration",
            top1 <= max_top1_share,
            f"top key {top1_key!r} covers {top1:.1%} of the population "
            f"(ceiling {max_top1_share:.0%})",
        ),
    ]


def check_redundancy(join_key_expr: str, exclude_signal: str | None = None) -> Check:
    """Is an existing signal already keyed on the same thing?

    Two sources keyed on the same column produce correlated constants, not
    corroboration. otx_threat_intel already keys on host.
    """
    assert_columns(SIGNAL_TABLE, ["signal_name", "score", "server_id"])
    rows = query(
        "SELECT signal_name, COUNT(*) AS n, COUNT(DISTINCT score) AS distinct_scores, "
        f"COUNT(DISTINCT server_id) AS servers FROM {SIGNAL_TABLE} "
        "WHERE signal_name IS NOT NULL GROUP BY 1 ORDER BY n DESC"
    )
    degenerate = [
        r for r in rows
        if r["n"] >= 1000 and r["distinct_scores"] <= 5 and r["signal_name"] != exclude_signal
    ]
    if degenerate:
        names = ", ".join(
            f"{r['signal_name']}({r['distinct_scores']} distinct/{r['n']} rows)"
            for r in degenerate[:5]
        )
        return Check(
            "non_redundancy",
            False,
            f"existing signals are already degenerate on this population: {names}. "
            "Adding another source keyed the same way buys a correlated constant.",
            {"degenerate_signals": degenerate[:10]},
        )
    return Check("non_redundancy", True, "no existing degenerate signal on this key")


def check_budget(
    signal_name: str,
    population: int,
    cost_per_lookup: int,
    monthly_budget: int | None,
) -> Check:
    """What does this source cost at the cadence the pipeline actually runs?

    Measured from the table, not assumed: url_safety is rewritten for the whole
    working set every hour (~1,800 rows/hour, avg 778 rows per server). A metered
    vendor wired into that loop bills 24x its useful rate every day.
    """
    assert_columns(SIGNAL_TABLE, ["signal_name", "scored_at"])
    rows = query(
        "SELECT COUNT(*) AS n FROM (SELECT 1 FROM "
        f"{SIGNAL_TABLE} WHERE signal_name='{signal_name}' "
        "AND scored_at > now() - INTERVAL 24 HOUR) t"
    )
    writes_24h = rows[0]["n"] if rows else 0
    rescore_factor = max(1.0, writes_24h / population) if population else 1.0
    daily = int(population * rescore_factor * cost_per_lookup)
    monthly = daily * 30

    ev = {
        "population": population,
        "observed_writes_24h": writes_24h,
        "rescore_factor_per_day": round(rescore_factor, 1),
        "lookups_per_day": daily,
        "lookups_per_month": monthly,
        "monthly_budget": monthly_budget,
    }
    if monthly_budget is None:
        return Check(
            "budget", False,
            f"no budget declared; this source would bill ~{monthly:,} lookups/month "
            f"at the observed cadence ({rescore_factor:.0f}x rescore/day)",
            ev,
        )
    return Check(
        "budget",
        monthly <= monthly_budget,
        f"~{monthly:,} lookups/month at the observed cadence "
        f"({rescore_factor:.0f}x rescore/day) vs budget {monthly_budget:,}",
        ev,
    )


def preflight(
    source: str,
    population_sql: str,
    join_key_expr: str,
    signal_name: str,
    cost_per_lookup: int = 1,
    monthly_budget: int | None = None,
    credits_available: int | None = None,
    min_distinct: int = MIN_DISTINCT_KEYS,
    max_top1_share: float = MAX_TOP1_SHARE,
) -> PreflightVerdict:
    """Run every gate. Any failure blocks the source from writing."""
    checks: list[Check] = []
    checks.extend(check_discrimination(population_sql, join_key_expr, min_distinct, max_top1_share))
    checks.append(check_redundancy(join_key_expr, exclude_signal=signal_name))

    population = checks[0].evidence.get("population", 0)
    checks.append(check_budget(signal_name, population, cost_per_lookup, monthly_budget))

    if credits_available is not None:
        needed = checks[0].evidence.get("distinct_keys", 0) * cost_per_lookup
        checks.append(
            Check(
                "credits",
                credits_available >= needed and credits_available > 0,
                f"{credits_available:,} credits available, {needed:,} needed for one "
                "deduplicated pass",
                {"credits_available": credits_available, "credits_needed": needed},
            )
        )
    return PreflightVerdict(source=source, checks=checks)


# --- CLI --------------------------------------------------------------------
REGISTRY_POPULATION = (
    "SELECT server_id, url FROM mcp_server_registry WHERE url LIKE 'http%'"
)
HOST_KEY = "lower(regexp_extract(url, '^https?://([^/:]+)', 1))"


def _self_test() -> int:
    """Prove both poles. A gate only trusted on the case it was built for is
    a prediction; these are measurements."""
    print("=" * 72)
    print("SELF-TEST 1 -- live registry keyed on host: expect BLOCKED")
    print("=" * 72)
    live = preflight(
        source="self-test-live",
        population_sql=REGISTRY_POPULATION,
        join_key_expr=HOST_KEY,
        signal_name="__selftest__",
        monthly_budget=25_000,
    )
    print(live.render())
    if live.armed:
        print("\nFAIL: gate armed on a population with 4 distinct hosts.")
        return 1

    print()
    print("=" * 72)
    print("SELF-TEST 2 -- synthetic discriminating population: expect ARMED")
    print("=" * 72)
    synthetic = (
        "SELECT 'srv-' || CAST(i AS VARCHAR) AS server_id, "
        "'https://host-' || CAST(i AS VARCHAR) || '.example.net/mcp' AS url "
        "FROM range(1, 200) t(i)"
    )
    synth = preflight(
        source="self-test-synthetic",
        population_sql=synthetic,
        join_key_expr=HOST_KEY,
        signal_name="__selftest__",
        monthly_budget=25_000,
        credits_available=25_000,
    )
    print(synth.render())

    # The synthetic population is perfectly discriminating, so only the
    # non_redundancy check may legitimately fail -- it reads the live table.
    fatal = [c for c in synth.blockers if c.name != "non_redundancy"]
    if fatal:
        print(f"\nFAIL: gate blocked a discriminating population on {[c.name for c in fatal]}")
        return 1

    print("\nBoth poles measured. Gate discriminates.")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--self-test", action="store_true", help="prove the gate blocks and arms correctly")
    ap.add_argument("--source", default="candidate")
    ap.add_argument("--signal-name", default="__candidate__")
    ap.add_argument("--population-sql", default=REGISTRY_POPULATION)
    ap.add_argument("--join-key", default=HOST_KEY)
    ap.add_argument("--cost-per-lookup", type=int, default=1)
    ap.add_argument("--monthly-budget", type=int, default=None)
    ap.add_argument("--credits", type=int, default=None)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    if args.self_test:
        return _self_test()

    try:
        verdict = preflight(
            source=args.source,
            population_sql=args.population_sql,
            join_key_expr=args.join_key,
            signal_name=args.signal_name,
            cost_per_lookup=args.cost_per_lookup,
            monthly_budget=args.monthly_budget,
            credits_available=args.credits,
        )
    except PreflightError as exc:
        print(f"preflight[{args.source}]: BLOCKED (check could not be evaluated)\n  {exc}",
              file=sys.stderr)
        return 2

    print(json.dumps(verdict.to_evidence(), indent=2) if args.json else verdict.render())
    return 0 if verdict.armed else 1


if __name__ == "__main__":
    raise SystemExit(main())
