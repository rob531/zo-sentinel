#!/usr/bin/env python3
"""
gate_2_schema_contracts.py -- Static + dynamic schema/endpoint contract checks.

Catches the bug classes we hit this week:
    - endpoint_semantic_mismatch: SELECT sent to /execute (fire-and-forget)
    - port_mismatch: URL points to wrong port
    - missing_pk_constraint: ON CONFLICT used but table lacks UNIQUE/PK
    - payload_key_drift: ws_write dict key doesn't match target column
    - stale_schema_ref: code references a table that doesn't exist
    - protected_file_mutated: PROTECTED file hash drifted from baseline
    - protected_file_missing: PROTECTED file no longer exists

All HTTP calls go through the framework's throttled ws_query / ws_execute so
the single-writer rate limit is respected.

2026-04-17: added _check_protected_files() after ui_server preview incident.
Protected files are listed in sentinel_directive_generator.PROTECTED_FILES and
should not change unless a deliberate hand-edit is made and acknowledged via
rebaseline_protected_files.py. Drift fires protected_file_mutated.
"""
import hashlib
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, "/home/workspace/zo_sentinel/tests/gates")
from gate_framework import Gate, gate_run, ws_query, WS
import requests
from gate_framework import _throttle  # reuse framework throttle for direct calls

SENTINEL = Path("/home/workspace/zo_sentinel")

DAEMONS = [
    "mcp_scanner.py",
    "signal_analyser.py",
    "trust_synthesiser.py",
    "threat_intel_ingestor.py",
    "risk_ranker.py",
    "attestation_engine.py",
    "rug_pull_monitor.py",
]

ALLOWED_PORTS = {"8772", "8771", "8781", "11434", "8790"}
# 8790 added 2026-04-17: ui_server preview port is legitimate, not a misroute.

# Files that should not change without explicit acknowledgement.
# KEEP IN SYNC with sentinel_directive_generator.PROTECTED_FILES and
# with rebaseline_protected_files.PROTECTED_FILES.
PROTECTED_FILES = [
    ('signal_analyser.py', '/home/workspace/zo_sentinel/signal_analyser.py'),
    ('trust_synthesiser.py', '/home/workspace/zo_sentinel/trust_synthesiser.py'),
    ('write_service.py', '/home/workspace/zo_mesh/write_service.py'),
    ('inference_router_service.py', '/home/workspace/zo_mesh/inference_router_service.py'),
    ('full_schema_bootstrap.py', '/home/workspace/zo_sentinel/full_schema_bootstrap.py'),
    ('mcp_scanner.py', '/home/workspace/zo_sentinel/mcp_scanner.py'),
    ('registry_api.py', '/home/workspace/zo_sentinel/registry_api.py'),
    ('attestation_engine.py', '/home/workspace/zo_sentinel/attestation_engine.py'),
    ('threat_intel_ingestor.py', '/home/workspace/zo_sentinel/threat_intel_ingestor.py'),
    ('rug_pull_monitor.py', '/home/workspace/zo_sentinel/rug_pull_monitor.py'),
    ('ui_server.py', '/home/workspace/zo_sentinel/ui_server.py'),
    ('dashboard.html', '/home/workspace/zo_sentinel/dashboard.html'),
    ('sentinel_status.html', '/home/workspace/zo_sentinel/sentinel_status.html'),
    ('approval_workflow.py', '/home/workspace/zo_sentinel/approval_workflow.py'),
    ('search_api.py', '/home/workspace/zo_sentinel/search_api.py'),
    ('dashboard_api.py', '/home/workspace/zo_sentinel/dashboard_api.py'),
    ('forensic_detail_api.py', '/home/workspace/zo_sentinel/forensic_detail_api.py'),
    ('comparison_api.py', '/home/workspace/zo_sentinel/comparison_api.py'),
    ('advanced_filter_api.py', '/home/workspace/zo_sentinel/advanced_filter_api.py'),
    ('manual_override_api.py', '/home/workspace/zo_sentinel/manual_override_api.py'),
    ('bulk_assess_api.py', '/home/workspace/zo_sentinel/bulk_assess_api.py'),
]

# Regex helpers
REQUEST_POST_RE = re.compile(
    r"requests\.post\s*\(\s*"
    r"(?P<url>[A-Z_][A-Z_0-9]*|'[^']+'|\"[^\"]+\")"
    r"\s*,\s*"
    r"json\s*=\s*\{(?P<body>[^{}]*(?:\{[^{}]*\}[^{}]*)*)\}",
    re.DOTALL,
)
SQL_FIELD_RE = re.compile(
    r"['\"]sql['\"]\s*:\s*(?P<sql>(?:\"\"\".+?\"\"\"|'''.+?'''|\"[^\"]*\"|'[^']*'))",
    re.DOTALL,
)
PAYLOAD_RE = re.compile(
    r"['\"]table['\"]\s*:\s*['\"]([a-z_]+)['\"][^}]*?"
    r"['\"]rows['\"]\s*:\s*(\{[^}]*\})",
    re.DOTALL,
)


def _file_fingerprint(path: Path) -> tuple[str, datetime, int] | None:
    """Return (sha256_hex, mtime_utc, size_bytes) or None if file is missing."""
    if not path.is_file():
        return None
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    stat = path.stat()
    mtime = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc)
    return h.hexdigest(), mtime, stat.st_size


class Gate2SchemaContracts(Gate):
    name = "gate_2_schema_contracts"

    def run(self):
        print(f"\n-- {self.name} --")
        self._check_port_references()
        self._check_endpoint_semantics()
        self._check_payload_keys_match_columns()
        self._check_pk_constraints()
        self._check_live_endpoints()
        self._check_protected_files()

    # -----------------------------------------------------------------
    def _check_port_references(self):
        for daemon in DAEMONS:
            path = SENTINEL / daemon
            if not path.exists():
                continue
            src = path.read_text()
            ports = set(re.findall(r"http://127\.0\.0\.1:(\d{4,5})/", src))
            bad = ports - ALLOWED_PORTS
            self.check(
                f"{daemon}: port references in allowed set",
                condition=(not bad),
                error_class="port_mismatch",
                expected=f"ports in {sorted(ALLOWED_PORTS)}",
                actual=f"unexpected ports: {sorted(bad)}" if bad else "clean",
                file=str(path),
                remediation="Replace non-allowed ports with 8772 for write_service",
            )

    # -----------------------------------------------------------------
    def _check_endpoint_semantics(self):
        for daemon in DAEMONS:
            path = SENTINEL / daemon
            if not path.exists():
                continue
            src = path.read_text()

            url_map = {}
            for m in re.finditer(
                r"^([A-Z_][A-Z_0-9]*)\s*=\s*['\"]([^'\"]+)['\"]",
                src, re.MULTILINE
            ):
                name, value = m.group(1), m.group(2)
                if "/query" in value:
                    url_map[name] = "/query"
                elif "/execute" in value:
                    url_map[name] = "/execute"
                elif "/write" in value:
                    url_map[name] = "/write"

            misrouted_selects = []
            misrouted_writes = []
            for m in REQUEST_POST_RE.finditer(src):
                url_token = m.group("url").strip()
                body = m.group("body")
                if url_token.startswith(("'", '"')):
                    literal = url_token.strip("'\"")
                    endpoint = ("/query" if "/query" in literal else
                               "/execute" if "/execute" in literal else
                               "/write" if "/write" in literal else None)
                else:
                    endpoint = url_map.get(url_token)
                if endpoint is None:
                    continue
                sql_m = SQL_FIELD_RE.search(body)
                if not sql_m:
                    continue
                sql_text = sql_m.group("sql").strip("'\"").strip()
                first_word = sql_text.lstrip().split(None, 1)[0].upper() if sql_text else ""
                if endpoint == "/execute" and first_word == "SELECT":
                    misrouted_selects.append(sql_text[:80])
                elif endpoint == "/query" and first_word in (
                    "INSERT", "UPDATE", "DELETE", "CREATE", "DROP", "ALTER"
                ):
                    misrouted_writes.append(sql_text[:80])

            self.check(
                f"{daemon}: no SELECTs routed to /execute",
                condition=(not misrouted_selects),
                error_class="endpoint_semantic_mismatch",
                expected="SELECTs go to /query endpoint",
                actual=(f"found {len(misrouted_selects)} SELECT(s) on /execute: "
                       f"{misrouted_selects[0][:60]}...") if misrouted_selects else "clean",
                file=str(path),
                remediation="Route SELECTs through /query URL constant, not /execute",
            )
            self.check(
                f"{daemon}: no writes routed to /query",
                condition=(not misrouted_writes),
                error_class="endpoint_semantic_mismatch",
                expected="DML goes to /execute endpoint",
                actual=(f"found {len(misrouted_writes)} write(s) on /query")
                       if misrouted_writes else "clean",
                file=str(path),
                remediation="Route DML through /execute URL constant, not /query",
            )

    # -----------------------------------------------------------------
    def _discover_tables_referenced(self) -> dict:
        refs = {}
        for daemon in DAEMONS:
            path = SENTINEL / daemon
            if not path.exists():
                continue
            src = path.read_text()
            tables = {m.group(1) for m in PAYLOAD_RE.finditer(src)}
            if tables:
                refs[daemon] = (path, src, tables)
        return refs

    def _fetch_columns_for_tables(self, tables: set) -> dict:
        columns_by_table = {}
        for table in sorted(tables):
            try:
                rows = ws_query(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_schema = 'main' AND table_name = ?",
                    params=[table],
                )
                columns_by_table[table] = {r["column_name"] for r in rows}
            except Exception:
                self.check(
                    f"info_schema lookup for '{table}'",
                    condition=False,
                    error_class="infra_unreachable",
                    actual=f"column query failed for table {table}",
                )
        return columns_by_table

    def _check_payload_keys_match_columns(self):
        daemon_refs = self._discover_tables_referenced()
        if not daemon_refs:
            return
        all_referenced_tables = set()
        for _, _, tables in daemon_refs.values():
            all_referenced_tables |= tables
        columns_by_table = self._fetch_columns_for_tables(all_referenced_tables)

        for daemon, (path, src, _) in daemon_refs.items():
            for m in PAYLOAD_RE.finditer(src):
                table = m.group(1)
                rows_block = m.group(2)
                live_cols = columns_by_table.get(table)
                if live_cols is None:
                    continue
                if not live_cols:
                    self.check(
                        f"{daemon}: table '{table}' exists in DB",
                        condition=False,
                        error_class="stale_schema_ref",
                        expected=f"table '{table}' in information_schema",
                        actual="table not found (0 columns returned)",
                        file=str(path),
                        remediation=f"Add {table} to full_schema_bootstrap.py "
                                   f"OR correct daemon to use a real table name",
                    )
                    continue
                dict_keys = set(re.findall(r"['\"]([a-z_]+)['\"]\s*:", rows_block))
                unknown = dict_keys - live_cols
                self.check(
                    f"{daemon}: ws_write('{table}') keys match columns",
                    condition=(not unknown),
                    error_class="payload_key_drift",
                    expected=f"keys subset of {table} columns",
                    actual=f"unknown keys: {sorted(unknown)}" if unknown else "clean",
                    file=str(path),
                    remediation=f"Rename dict keys to match {table} columns, "
                               f"or add missing columns to the table schema",
                )

    # -----------------------------------------------------------------
    def _check_pk_constraints(self):
        try:
            cons_rows = ws_query(
                "SELECT table_name, constraint_type FROM duckdb_constraints() "
                "WHERE constraint_type IN ('PRIMARY KEY','UNIQUE')"
            )
        except Exception as e:
            self.check("duckdb_constraints() reachable",
                      condition=False,
                      error_class="infra_unreachable", actual=str(e))
            return

        has_key_constraint = {r["table_name"] for r in cons_rows}
        upsert_tables = {"mcp_signal_scores", "mcp_threat_associations",
                         "mcp_server_registry", "mcp_risk_register",
                         "mcp_attestations", "mcp_signal_enrichments"}

        for table in upsert_tables:
            self.check(
                f"table '{table}' has PK or UNIQUE constraint",
                condition=(table in has_key_constraint),
                error_class="missing_pk_constraint",
                expected="PRIMARY KEY or UNIQUE present",
                actual="none found" if table not in has_key_constraint else "present",
                remediation=f"Add PRIMARY KEY or UNIQUE to {table} in "
                           "full_schema_bootstrap.py so it survives reboots",
            )

    # -----------------------------------------------------------------
    def _check_live_endpoints(self):
        try:
            _throttle()
            r = requests.post(WS + "/query",
                              json={"sql": "SELECT 1 AS n"}, timeout=10)
            ok = (r.status_code == 200 and "rows" in r.json())
            self.check(
                "/query returns {rows: [...]}",
                condition=ok,
                error_class="endpoint_response_shape",
                expected='{"rows": [...]}',
                actual=r.text[:100],
            )
        except Exception as e:
            self.check("/query endpoint reachable",
                      condition=False,
                      error_class="infra_unreachable", actual=str(e))

        try:
            _throttle()
            r = requests.post(WS + "/execute",
                              json={"sql": "SELECT 1", "wait": True}, timeout=10)
            body = r.json() if r.status_code == 200 else {}
            ok = (body.get("ok") is True and "rows" not in body)
            self.check(
                "/execute returns {ok: true} without rows",
                condition=ok,
                error_class="endpoint_response_shape",
                expected='{"ok": true} (no rows key)',
                actual=r.text[:100],
                remediation="If rows are returned, write_service contract has changed "
                           "and daemons expecting fire-and-forget may block",
            )
        except Exception as e:
            self.check("/execute endpoint reachable",
                      condition=False,
                      error_class="infra_unreachable", actual=str(e))

    # -----------------------------------------------------------------
    def _check_protected_files(self):
        """Verify each PROTECTED file's SHA256 matches its baseline.

        First observation of a file auto-baselines (logs one-time "baselined"
        informational). Subsequent observations compare hashes:
          - hash matches  -> pass
          - hash differs  -> fail with protected_file_mutated
          - file missing  -> fail with protected_file_missing

        mtime alone is NOT considered drift (editors often re-write unchanged
        content). Only content hash drift fires an error.

        To acknowledge a legitimate change, run:
            python3 /home/workspace/zo_sentinel/tests/rebaseline_protected_files.py <name>
        """
        # Schema survival check: if baseline table doesn't exist, skip gracefully
        # rather than fire 21 infra_unreachable errors.
        try:
            self.db.con.execute(
                "SELECT 1 FROM protected_file_baseline LIMIT 1"
            ).fetchone()
        except Exception:
            self.check(
                "protected_file_baseline table present",
                condition=False,
                error_class="infra_unreachable",
                actual="table missing",
                remediation="Run: python3 /home/workspace/zo_sentinel/tests/"
                           "seed_protected_file_baseline.py",
            )
            return

        for name, abs_path_str in PROTECTED_FILES:
            path = Path(abs_path_str)
            fp = _file_fingerprint(path)

            # Missing file
            if fp is None:
                self.check(
                    f"protected file exists: {name}",
                    condition=False,
                    error_class="protected_file_missing",
                    expected=f"{name} exists at {path}",
                    actual="file not found on disk",
                    file=str(path),
                    remediation=(
                        f"Restore {name} from most recent .bak in same directory "
                        "or from git. If removal was intentional, remove from "
                        "PROTECTED_FILES in gate_2_schema_contracts.py AND "
                        "rebaseline_protected_files.py."
                    ),
                )
                continue

            sha, mtime, size = fp
            baseline = self.db.con.execute(
                "SELECT sha256, baselined_at FROM protected_file_baseline "
                "WHERE path = ?",
                [name],
            ).fetchone()

            if baseline is None:
                # First observation -- auto-baseline and record informational check
                self.db.con.execute(
                    "INSERT INTO protected_file_baseline "
                    "(path, sha256, mtime, size_bytes, baselined_by, reason) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    [name, sha, mtime, size, "gate_2_auto",
                     "first observation by gate_2"],
                )
                self.check(
                    f"protected file baselined: {name}",
                    condition=True,
                )
                # Note the baseline event explicitly -- do NOT fire as error
                # (no self.check(False, ...) call) because it's informational
                continue

            # Compare hashes
            baseline_sha, baselined_at = baseline
            matches = (sha == baseline_sha)
            self.check(
                f"protected file unchanged: {name}",
                condition=matches,
                error_class="protected_file_mutated",
                expected=f"sha256={baseline_sha[:16]}... (baselined {baselined_at})",
                actual=f"sha256={sha[:16]}... size={size}",
                file=str(path),
                remediation=(
                    f"File {name} has been modified since last baseline. If the "
                    f"change is intentional, acknowledge with: python3 "
                    f"/home/workspace/zo_sentinel/tests/rebaseline_protected_files.py "
                    f"{name}  -- otherwise investigate (check .bak files, builder logs)."
                ),
            )


def main() -> int:
    with gate_run(trigger="manual", host_state="steady-state") as (db, run_id):
        gate = Gate2SchemaContracts(db, run_id)
        gate.run()
        print(f"\nGate 2: {gate.checks - gate.failures}/{gate.checks} checks passed")
        return 1 if gate.failures else 0


if __name__ == "__main__":
    sys.exit(main())