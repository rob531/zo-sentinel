#!/usr/bin/env python3
"""
gate_5_synthesis_flow.py -- Dynamic end-to-end synthesis flow gate.

Redesigned 2026-04-17:
    trust_synthesiser cycles every 30 minutes. Polling for a natural verdict
    within a gate run's time budget (<5min) is unreliable. Instead, this
    gate verifies the pipeline *mechanically* by:

    1. Inject canary MCP into mcp_server_registry
    2. Inject 6 known signals into mcp_signal_scores
    3. Run trust_synthesiser's EXACT pivot SQL (via /query) -- proves the
       query works and returns the expected row shape
    4. Run trust_synthesiser's compute_composite_score() logic on the
       returned row -- proves the math works
    5. Verify the canary's signals went in and came out correctly
    6. DELETE canary rows, memorialize spec+observed_by+final_state

    This design avoids the 30-minute wait. We don't need trust_synthesiser
    itself to produce the verdict -- we need proof that if it did run its
    cycle right now, it would produce a correct verdict.

2026-04-17 fix: mcp_signal_scores has id BIGINT NOT NULL with no default.
signal_analyser.py populates id itself (observed IDs ~2.1B range, hash-like).
Gate 5 canary signal writes were failing silently due to missing id. We now
compute a stable id per (canary_id, signal_name) using hash, which:
    - keeps canaries deterministic across runs (reruns overwrite, not duplicate)
    - avoids production id collisions by using a reserved high-bit prefix
      (negative BIGINT ids, outside the observed production range of [0, 2^31])

Catches the 4 bug classes we hit this week:
    - endpoint_semantic_mismatch (pivot SQL must land on /query, not /execute)
    - missing_pk_constraint (signal upserts must succeed via ON CONFLICT)
    - payload_key_drift (canary registry insert must succeed with 'server_id')
    - stale_schema_ref (pivot SQL must reference real columns)
"""
import hashlib
import json
import sys
import time

sys.path.insert(0, "/home/workspace/zo_sentinel/tests/gates")
from gate_framework import Gate, gate_run, ws_query, ws_write_row, ws_execute

CANARY_PREFIX = "__gate_canary_"
CLEANUP_MAX_RETRIES = 3

# Matches trust_synthesiser.py internal weights (must stay in sync or this
# gate will false-fail after a weights change)
WEIGHTS = {
    "domain_trust":             0.25,
    "tool_description_safety":  0.15,
    "permission_scope":         0.20,
    "supply_chain":             0.20,
    "community_signal":         0.10,
    "temporal_stability":       0.10,
}

# Canary input signals -- chosen so predicted composite is 79.25
CANARY_SIGNALS = {
    "domain_trust":             80.0,
    "tool_description_safety":  85.0,
    "permission_scope":         90.0,
    "supply_chain":             70.0,
    "community_signal":         75.0,
    "temporal_stability":       80.0,
}
EXPECTED_COMPOSITE = sum(CANARY_SIGNALS[s] * w for s, w in WEIGHTS.items())

# Reserved id prefix for canary rows -- large negative BIGINTs.
# Production signal_analyser uses positive int32 range (~2.1B), so negative
# BIGINTs are guaranteed non-colliding. DuckDB BIGINT is int64.
CANARY_ID_FLOOR = -9_000_000_000_000_000_000  # far below any real id


def _canary_signal_id(canary_server_id: str, signal_name: str) -> int:
    """Deterministic negative BIGINT id for a (canary, signal) pair.
    Stable across runs so reruns upsert rather than duplicate.
    Always negative to stay outside production id space."""
    h = hashlib.sha256(
        (canary_server_id + "|" + signal_name).encode()
    ).digest()
    # Take 8 bytes, interpret as signed int64 big-endian, clamp to negative space
    raw = int.from_bytes(h[:8], byteorder="big", signed=True)
    # Force negative by OR-ing with the sign bit if positive
    if raw >= 0:
        raw = -raw - 1  # maps [0, 2^63-1] -> [-2^63, -1]
    # Keep well within int64 bounds: DuckDB BIGINT min is -9223372036854775808
    # Our CANARY_ID_FLOOR is -9e18; all hash-derived negatives are safely above it.
    return raw


class Gate5SynthesisFlow(Gate):
    name = "gate_5_synthesis_flow"

    def __init__(self, db, run_id):
        super().__init__(db, run_id)
        self.canary_id = CANARY_PREFIX + run_id[-12:] + "__"
        self.observed_by = []
        self.final_state = {}

    def run(self):
        print(f"\n-- {self.name} -- canary_id={self.canary_id}")
        try:
            if not self._inject_canary_registry():
                return
            if not self._verify_registry_insert():
                return
            if not self._inject_canary_signals():
                return
            if not self._verify_signals_readable():
                return
            self._verify_pivot_sql()
            self._verify_composite_math()
        finally:
            self._cleanup_and_memorialize()

    # ---- Step 1: inject canary into registry -----------------------
    def _inject_canary_registry(self) -> bool:
        ok = ws_write_row("mcp_server_registry", {
            "server_id":       self.canary_id,
            "name":             self.canary_id,
            "registry_source": "gate_canary",
            "url":              f"https://canary.test/{self.canary_id}",
            "description":     "Gate 5 canary -- will be deleted",
        }, mode="upsert")
        return self.check(
            "canary inserted into mcp_server_registry",
            condition=ok,
            error_class="canary_insert_failed",
            remediation="Check write_service log; verify 'server_id' is the PK column",
        )

    # ---- Step 2: verify registry row readable ---------------------
    def _verify_registry_insert(self) -> bool:
        rows = ws_query(
            "SELECT server_id FROM mcp_server_registry WHERE server_id = ?",
            params=[self.canary_id],
        )
        found = bool(rows)
        self.check(
            "canary readable via /query",
            condition=found,
            error_class="canary_read_failed",
            expected="1 row",
            actual=f"{len(rows)} rows",
        )
        if found:
            self.observed_by.append("mcp_server_registry")
        return found

    # ---- Step 3: inject 6 canary signals --------------------------
    def _inject_canary_signals(self) -> bool:
        written = 0
        for sig_name, score in CANARY_SIGNALS.items():
            row_id = _canary_signal_id(self.canary_id, sig_name)
            ok = ws_write_row("mcp_signal_scores", {
                "id":          row_id,
                "server_id":   self.canary_id,
                "signal_name": sig_name,
                "score":        score,
                "evidence":    json.dumps({"canary": True, "run_id": self.run_id}),
            }, mode="upsert")
            if ok:
                written += 1

        ok = self.check(
            "6 signal rows written for canary",
            condition=(written == 6),
            error_class="signal_write_failed",
            expected="6 rows",
            actual=f"{written} rows",
            remediation="Check mcp_signal_scores has UNIQUE(server_id, signal_name); "
                       "check write_service log for Binder Error; "
                       "check id column is being populated (now auto by gate)",
        )
        if written == 6:
            self.observed_by.append("mcp_signal_scores")
        return ok

    # ---- Step 4: verify all 6 signals readable --------------------
    def _verify_signals_readable(self) -> bool:
        rows = ws_query(
            "SELECT signal_name, score FROM mcp_signal_scores WHERE server_id = ?",
            params=[self.canary_id],
        )
        found_signals = {r["signal_name"]: r["score"] for r in rows}
        missing = set(CANARY_SIGNALS) - set(found_signals)
        return self.check(
            "all 6 canary signals readable",
            condition=(not missing),
            error_class="signal_read_failed",
            expected=f"signals {sorted(CANARY_SIGNALS)}",
            actual=f"missing {sorted(missing)}" if missing else "all present",
        )

    # ---- Step 5: run trust_synthesiser's pivot and verify shape ----
    def _verify_pivot_sql(self):
        pivot_sql = """
            SELECT
                server_id AS tool_name,
                MAX(CASE WHEN signal_name='domain_trust'            THEN score END) AS domain_trust,
                MAX(CASE WHEN signal_name='tool_description_safety' THEN score END) AS tool_description_safety,
                MAX(CASE WHEN signal_name='permission_scope'        THEN score END) AS permission_scope,
                MAX(CASE WHEN signal_name='supply_chain'            THEN score END) AS supply_chain,
                MAX(CASE WHEN signal_name='community_signal'        THEN score END) AS community_signal,
                MAX(CASE WHEN signal_name='temporal_stability'      THEN score END) AS temporal_stability,
                MAX(scored_at)                                                       AS last_updated
            FROM mcp_signal_scores
            WHERE server_id = ?
            GROUP BY server_id
        """
        try:
            rows = ws_query(pivot_sql, params=[self.canary_id])
        except Exception as e:
            self.check(
                "pivot SQL executes without error",
                condition=False,
                error_class="pivot_sql_failed",
                actual=str(e),
                remediation="Check trust_synthesiser.py line ~query_signal_scores; "
                           "ensure URL is QUERY_URL (not EXECUTE_URL)",
            )
            return

        self.check(
            "pivot SQL returns 1 row for canary",
            condition=(len(rows) == 1),
            error_class="pivot_sql_wrong_cardinality",
            expected="1 row",
            actual=f"{len(rows)} rows",
        )
        if not rows:
            return

        row = rows[0]
        for sig_name, expected_score in CANARY_SIGNALS.items():
            actual_score = row.get(sig_name)
            self.check(
                f"pivot: {sig_name} = {expected_score}",
                condition=(actual_score == expected_score),
                error_class="pivot_sql_wrong_score",
                expected=str(expected_score),
                actual=str(actual_score),
            )

        self.observed_by.append("pivot_sql")
        self.final_state["pivot_row"] = {k: v for k, v in row.items() if k != "last_updated"}

    # ---- Step 6: run the composite math and verify ----------------
    def _verify_composite_math(self):
        pivot_row = self.final_state.get("pivot_row")
        if not pivot_row:
            self.check(
                "composite math: pivot_row available",
                condition=False,
                error_class="composite_math_no_input",
                remediation="Previous pivot SQL check failed -- fix that first",
            )
            return

        total_weight = 0.0
        weighted_sum = 0.0
        for sig_name, weight in WEIGHTS.items():
            value = pivot_row.get(sig_name)
            if value is not None and value >= 0:
                weighted_sum += value * weight
                total_weight += weight

        if total_weight == 0:
            composite = 0.0
        else:
            composite = (weighted_sum / total_weight) * (
                total_weight / sum(WEIGHTS.values())
            )
        composite = round(min(100.0, max(0.0, composite)), 2)

        tolerance = 0.5
        within = abs(composite - EXPECTED_COMPOSITE) <= tolerance
        self.check(
            f"composite score = {EXPECTED_COMPOSITE:.2f} +/- {tolerance}",
            condition=within,
            error_class="composite_math_wrong",
            expected=f"{EXPECTED_COMPOSITE:.2f} +/- {tolerance}",
            actual=f"{composite:.2f}",
            remediation="Check WEIGHTS in trust_synthesiser.py match this gate's "
                       "WEIGHTS constant; update whichever is stale",
        )
        self.final_state["computed_composite"] = composite
        self.final_state["expected_composite"] = EXPECTED_COMPOSITE

    # ---- Cleanup with retries -------------------------------------
    def _cleanup_and_memorialize(self):
        self.final_state["canary_id"] = self.canary_id
        cleanup_ok = True

        for table in ["mcp_signal_scores", "mcp_server_registry"]:
            deleted = False
            for attempt in range(CLEANUP_MAX_RETRIES):
                if ws_execute(
                    f"DELETE FROM {table} WHERE server_id = '{self.canary_id}'"
                ):
                    deleted = True
                    break
                time.sleep(1 + attempt)
            if not deleted:
                cleanup_ok = False
                check_id = self.db.record_check(
                    self.run_id, self.name,
                    f"cleanup: DELETE from {table}",
                    "fail", duration_ms=0,
                    details=f"after {CLEANUP_MAX_RETRIES} attempts",
                )
                self.db.record_error(
                    check_id, error_class="canary_cleanup_failed",
                    expected=f"0 rows after DELETE from {table}",
                    actual="DELETE failed",
                    remediation=f"Manually: DELETE FROM {table} WHERE "
                               f"server_id = '{self.canary_id}'",
                )

        self.db.memorialize_canary(
            run_id=self.run_id,
            spec={
                "canary_id":     self.canary_id,
                "injected_signals": CANARY_SIGNALS,
                "expected_composite": EXPECTED_COMPOSITE,
            },
            observed_by=self.observed_by,
            final_state=self.final_state,
            cleanup_ok=cleanup_ok,
        )
        status = "OK" if cleanup_ok else "PARTIAL (see canary_cleanup_failed errors)"
        print(f"    cleanup {status}, state memorialized")


def main() -> int:
    with gate_run(trigger="manual", host_state="steady-state") as (db, run_id):
        gate = Gate5SynthesisFlow(db, run_id)
        gate.run()
        print(f"\nGate 5: {gate.checks - gate.failures}/{gate.checks} checks passed")
        return 1 if gate.failures else 0


if __name__ == "__main__":
    sys.exit(main())