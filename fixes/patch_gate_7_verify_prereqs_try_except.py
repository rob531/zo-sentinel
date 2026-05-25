#!/usr/bin/env python3
"""
patch_gate_7_verify_prereqs_try_except.py

Commit 3.3 — stop Gate 7 from crashing the orchestrator when
write_service is down.

Root cause: gate_7_threat_flow.Gate7ThreatFlow._verify_prereqs() calls
ws_query() without try/except. When write_service returns HTTP 400
(\"database invalidated\"), ws_query raises RuntimeError, which bubbles
through run()'s try/finally (finally only runs cleanup; it doesn't
catch). The exception propagates to run_gates.py's orchestrator loop,
which crashes with an unhandled exception — stopping Gate 8 from ever
running.

The pattern for handling this already exists in _verify_join_query_shape
(further down the same file) -- try/except that records the failure as a
check with error_class='infra_unreachable' or 'pivot_sql_failed'. This
patcher applies the same pattern to _verify_prereqs.

After this patch, a poisoned write_service makes Gate 7 record
check-failures for each prereq table and return False. Gate 8 still
runs. Gate 7's own state file memorializes the infra problem.

Idempotent. AST-validated. Backup on write.
"""
import ast
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

TARGET = Path("/home/workspace/zo_sentinel/tests/gates/gate_7_threat_flow.py")

OLD = (
    "    # ---- Step 0: verify tables exist (per-table, L1) ----\n"
    "    def _verify_prereqs(self) -> bool:\n"
    "        \"\"\"Check world_articles and mcp_threat_associations schemas are present.\"\"\"\n"
    "        ok_all = True\n"
    "        for table, expected_cols in [\n"
    "            (\"world_articles\",           {\"id\", \"title\", \"url\", \"topics\"}),\n"
    "            (\"mcp_threat_associations\",  {\"server_id\", \"threat_type\", \"severity\"}),\n"
    "            (\"mcp_server_registry\",      {\"server_id\", \"name\"}),\n"
    "        ]:\n"
    "            rows = ws_query(\n"
    "                \"SELECT column_name FROM information_schema.columns \"\n"
    "                \"WHERE table_schema = 'main' AND table_name = ?\",\n"
    "                params=[table],\n"
    "            )\n"
    "            live_cols = {r[\"column_name\"] for r in rows}\n"
    "            missing = expected_cols - live_cols\n"
    "            ok = not missing\n"
    "            ok_all = ok_all and ok\n"
    "            self.check(\n"
    "                f\"prereq: {table} has expected columns\",\n"
    "                condition=ok,\n"
    "                error_class=\"stale_schema_ref\",\n"
    "                expected=f\"columns include {sorted(expected_cols)}\",\n"
    "                actual=f\"missing: {sorted(missing)}\" if missing else \"present\",\n"
    "            )\n"
    "        return ok_all"
)
NEW = (
    "    # ---- Step 0: verify tables exist (per-table, L1) ----\n"
    "    def _verify_prereqs(self) -> bool:\n"
    "        \"\"\"Check world_articles and mcp_threat_associations schemas are present.\n"
    "        Commit 3.3: catch ws_query failures so a poisoned write_service\n"
    "        doesn't crash the orchestrator. Failed queries record an\n"
    "        infra_unreachable check and Gate 7 bails gracefully, letting\n"
    "        Gate 8 run.\"\"\"\n"
    "        ok_all = True\n"
    "        for table, expected_cols in [\n"
    "            (\"world_articles\",           {\"id\", \"title\", \"url\", \"topics\"}),\n"
    "            (\"mcp_threat_associations\",  {\"server_id\", \"threat_type\", \"severity\"}),\n"
    "            (\"mcp_server_registry\",      {\"server_id\", \"name\"}),\n"
    "        ]:\n"
    "            try:\n"
    "                rows = ws_query(\n"
    "                    \"SELECT column_name FROM information_schema.columns \"\n"
    "                    \"WHERE table_schema = 'main' AND table_name = ?\",\n"
    "                    params=[table],\n"
    "                )\n"
    "            except Exception as e:\n"
    "                # write_service unreachable, poisoned, or 400ing.\n"
    "                # Record a check-failure (not a raise) so the\n"
    "                # orchestrator keeps going and Gate 8 runs.\n"
    "                self.check(\n"
    "                    f\"prereq: {table} reachable\",\n"
    "                    condition=False,\n"
    "                    error_class=\"infra_unreachable\",\n"
    "                    expected=\"information_schema query returns rows\",\n"
    "                    actual=f\"ws_query raised: {type(e).__name__}: {str(e)[:180]}\",\n"
    "                    remediation=(\n"
    "                        \"Check write_service status at :8772/health. \"\n"
    "                        \"If DuckDB is invalidated, kill write_service \"\n"
    "                        \"so the wrapper respawns it (commit 3.1 does \"\n"
    "                        \"this automatically via os._exit(42)).\"\n"
    "                    ),\n"
    "                )\n"
    "                ok_all = False\n"
    "                continue\n"
    "            live_cols = {r[\"column_name\"] for r in rows}\n"
    "            missing = expected_cols - live_cols\n"
    "            ok = not missing\n"
    "            ok_all = ok_all and ok\n"
    "            self.check(\n"
    "                f\"prereq: {table} has expected columns\",\n"
    "                condition=ok,\n"
    "                error_class=\"stale_schema_ref\",\n"
    "                expected=f\"columns include {sorted(expected_cols)}\",\n"
    "                actual=f\"missing: {sorted(missing)}\" if missing else \"present\",\n"
    "            )\n"
    "        return ok_all"
)


def _backup(path):
    ts = datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')
    bak = path.with_suffix(path.suffix + f".bak.{ts}")
    shutil.copy2(path, bak)
    print(f"  [backup] {bak.name}")


def main():
    print("=" * 60)
    print("gate_7_threat_flow: _verify_prereqs try/except (commit 3.3)")
    print("=" * 60)

    if not TARGET.exists():
        print(f"  [FAIL] target not found: {TARGET}")
        return 2
    src = TARGET.read_text()

    if "Commit 3.3: catch ws_query failures" in src:
        print("  [skip] patch already present")
        return 0

    if OLD not in src:
        print("  [FAIL] _verify_prereqs anchor not found verbatim")
        print("  Inspect the target file by hand")
        return 2

    src = src.replace(OLD, NEW, 1)
    print("  [patch] _verify_prereqs now tolerates ws_query failures")

    try:
        ast.parse(src)
    except SyntaxError as e:
        print(f"  [FAIL] AST invalid after patch: {e}")
        return 2

    _backup(TARGET)
    TARGET.write_text(src)
    print(f"\n  [done] {TARGET.name} patched")
    print("\nVerify AST:")
    print('  python3 -c "import ast; ast.parse(open(\'/home/workspace/zo_sentinel/tests/gates/gate_7_threat_flow.py\').read()); print(\'AST OK\')"')
    print("\nTest (once write_service is up):")
    print("  python3 /home/workspace/zo_sentinel/tests/gates/run_gates.py 7")
    print("  # Should pass normally when write_service is healthy")
    print("\nNegative test (simulate write_service down):")
    print("  pkill -9 -f 'write_service.py'")
    print("  # Wait 10s so wrapper doesn't respawn instantly")
    print("  python3 /home/workspace/zo_sentinel/tests/gates/run_gates.py")
    print("  # Expect: Gate 7 reports infra_unreachable for 3 tables but")
    print("  #         orchestrator continues; Gate 8 runs; rc=1 (failures) but no crash")
    return 0


if __name__ == "__main__":
    sys.exit(main())