#!/usr/bin/env python3
"""
gate_9_signal_diversity.py  -- commit 4.3

Signal diversity gate. Fails when any signal in mcp_signal_scores has
insufficient distinct-value discrimination across the MCP population.

Rationale: a signal with only 1 distinct value across all MCPs
contributes nothing to the final verdict. This is functionally the
same as not having the signal at all -- but worse, because downstream
code thinks the signal exists and weights it. The silent-uniformity
failure mode (memory notes: '5/6 signals had 1 distinct value') cost
weeks of discrimination quality before it was noticed.

Gate 9 makes it loud.

Contract:
  - Every signal MUST have >=2 distinct values across the population
  - Weighted by coverage: a signal with <10% of MCPs scored doesn't
    count (insufficient data)
  - Reports distinct count + stddev + range so it's obvious what's
    flat vs just bucketed

Severity tiers:
  - FAIL: distinct=1 and coverage>=10%  (silent uniformity)
  - FAIL: coverage<10% AND population>=50 (signal isn't being populated)
  - WARN (pass w/ note): distinct in [2,3] and coverage>=50% (bucketed,
    not ideal but not actionable now)
  - PASS: distinct>=4 OR stddev>5.0

Looks at mcp_signal_scores table. Does NOT test enrichment modules
directly -- those are upstream. Gate 9 tests OUTPUTS.
"""
import sys
from pathlib import Path

sys.path.insert(0, "/home/workspace/zo_sentinel/tests/gates")
sys.path.insert(0, "/home/workspace/zo_sentinel")
from gate_framework import Gate, gate_run, ws_query


# Tunables
MIN_POPULATION = 50        # below this, coverage metric is noisy
MIN_COVERAGE_PCT = 10      # below this, signal is not being populated
WARN_COVERAGE_PCT = 50     # above this, bucketed signals trigger WARN
MIN_DISTINCT_FAIL = 2      # fewer distinct values = silent uniformity (fail)
MIN_DISTINCT_PASS = 4      # at or above this = fully acceptable
MIN_STDDEV_PASS = 5.0      # OR stddev above this = acceptable (high-discrim)


class Gate9SignalDiversity(Gate):
    name = "gate_9_signal_diversity"

    def run(self):
        print(f"\n-- {self.name} --")

        # 1. Total MCP population
        try:
            total_rows = ws_query(
                "SELECT COUNT(*) AS n FROM mcp_server_registry"
            )
            total_mcps = int(total_rows[0]["n"]) if total_rows else 0
        except Exception as e:
            self.check(
                "gate_9: mcp_server_registry reachable",
                condition=False,
                error_class="infra_unreachable",
                actual=str(e)[:200],
                remediation="Check write_service :8772",
            )
            return

        if total_mcps < MIN_POPULATION:
            self.check(
                "gate_9: population threshold",
                condition=True,
                actual=f"only {total_mcps} MCPs; diversity analysis suppressed",
            )
            print(f"    [info] population below {MIN_POPULATION} ({total_mcps}); gate idle")
            return

        # 2. Per-signal discrimination stats
        try:
            sig_rows = ws_query(
                """
                SELECT signal_name,
                       COUNT(DISTINCT score) AS distinct_vals,
                       COUNT(*)              AS row_count,
                       ROUND(AVG(score), 3)  AS avg_score,
                       ROUND(MIN(score), 3)  AS lo,
                       ROUND(MAX(score), 3)  AS hi,
                       ROUND(STDDEV(score), 4) AS stddev
                FROM mcp_signal_scores
                GROUP BY signal_name
                ORDER BY signal_name
                """
            )
        except Exception as e:
            self.check(
                "gate_9: mcp_signal_scores reachable",
                condition=False,
                error_class="infra_unreachable",
                actual=str(e)[:200],
                remediation="Check write_service :8772",
            )
            return

        if not sig_rows:
            self.check(
                "gate_9: signal_scores populated",
                condition=False,
                error_class="no_signals_scored",
                expected="at least one signal has rows in mcp_signal_scores",
                actual="zero rows returned",
                remediation=(
                    "signal_analyser has not written any scores yet. "
                    "Check if it's running and reaching write_service."
                ),
            )
            return

        # 3. Evaluate each signal
        print(f"    [info] population: {total_mcps} MCPs; evaluating {len(sig_rows)} signals")
        flat_signals = []
        sparse_signals = []
        bucketed_signals = []

        for r in sig_rows:
            sig_name = r["signal_name"]
            distinct = int(r["distinct_vals"])
            rows = int(r["row_count"])
            stddev = float(r["stddev"] or 0.0)
            lo = float(r["lo"] or 0.0)
            hi = float(r["hi"] or 0.0)
            coverage_pct = (rows / total_mcps * 100.0) if total_mcps else 0.0

            detail = (
                f"distinct={distinct} rows={rows} coverage={coverage_pct:.1f}% "
                f"range=[{lo},{hi}] stddev={stddev:.3f}"
            )

            # FAIL: silent uniformity
            if distinct < MIN_DISTINCT_FAIL and coverage_pct >= MIN_COVERAGE_PCT:
                flat_signals.append(sig_name)
                self.check(
                    f"gate_9: {sig_name} discriminates",
                    condition=False,
                    error_class="signal_uniformity",
                    expected=f">= {MIN_DISTINCT_FAIL} distinct values at >={MIN_COVERAGE_PCT}% coverage",
                    actual=detail,
                    remediation=(
                        f"Signal '{sig_name}' produces 1 distinct value across "
                        f"{rows} MCPs. Either the enrichment module is always "
                        "hitting the same code path, or signal_analyser is "
                        "falling back to a default. Investigate: "
                        "(1) SELECT DISTINCT evidence FROM mcp_signal_scores "
                        f"WHERE signal_name='{sig_name}' LIMIT 5; "
                        "(2) check enrichment module logic for edge cases."
                    ),
                )
                continue

            # FAIL: coverage gap (signal exists but isn't being populated)
            if coverage_pct < MIN_COVERAGE_PCT:
                sparse_signals.append(sig_name)
                self.check(
                    f"gate_9: {sig_name} coverage",
                    condition=False,
                    error_class="signal_coverage_gap",
                    expected=f"signal scored for >={MIN_COVERAGE_PCT}% of MCPs",
                    actual=detail,
                    remediation=(
                        f"Signal '{sig_name}' scored for only {rows}/{total_mcps} MCPs. "
                        "Either the enrichment daemon isn't running, signal_bridge "
                        "isn't wiring it into mcp_signal_scores, or the upstream "
                        "logic has a bug that only produces scores for some MCPs."
                    ),
                )
                continue

            # WARN: bucketed but acceptable
            if (distinct < MIN_DISTINCT_PASS and stddev < MIN_STDDEV_PASS
                    and coverage_pct >= WARN_COVERAGE_PCT):
                bucketed_signals.append(sig_name)
                # Pass with a warning note in details; don't record as failure
                self.check(
                    f"gate_9: {sig_name} discriminates (bucketed)",
                    condition=True,
                    actual=detail + " [WARN: low distinct count]",
                )
                continue

            # PASS: good discrimination
            self.check(
                f"gate_9: {sig_name} discriminates",
                condition=True,
                actual=detail,
            )

        # Summary
        print(f"    [summary] flat={len(flat_signals)} sparse={len(sparse_signals)} "
              f"bucketed={len(bucketed_signals)} ok={len(sig_rows) - len(flat_signals) - len(sparse_signals) - len(bucketed_signals)}")
        if flat_signals:
            print(f"    [CRITICAL] flat signals: {flat_signals}")
        if sparse_signals:
            print(f"    [warn] sparse signals: {sparse_signals}")


def main() -> int:
    with gate_run(trigger="manual", host_state="steady-state") as (db, run_id):
        gate = Gate9SignalDiversity(db, run_id)
        gate.run()
        print(f"\nGate 9: {gate.checks - gate.failures}/{gate.checks} checks passed")
        return 1 if gate.failures else 0


if __name__ == "__main__":
    sys.exit(main())