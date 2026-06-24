#!/usr/bin/env python3
"""
smoke_ladder.py -- recursive (short-circuit) smoke-gate ladder for CI.

The ladder is a sequence of tiers ordered from cheapest/most-fundamental to
most-integrated. Each tier only runs if every tier below it fully passed --
that is the "recursive" structure: an inner failure short-circuits all outer
tiers, because there is no point import-testing code that won't compile, or
contract-testing a schema whose module won't import.

    Tier 0  syntax     py_compile every tracked *.py (minus quarantine)
    Tier 1  import      import the hermetic allowlist (manifest)
    Tier 2  schema      build committed schema snapshots in ephemeral DuckDB
    Tier 3  service     round-trip the mock write_service contract

Tiers 0-2 need nothing but the checkout + deps. Tier 3 needs a mock
write_service reachable at $ZO_WRITE_SERVICE (run_ci_smoke.py starts one).

Outputs:
    - console summary (human)
    - junit XML at $CI_SMOKE_JUNIT (default artifacts/ci_smoke_junit.xml),
      which gh_actions_fetcher.py reverse-feeds into mesh_memory
    - process exit code: 0 all-pass, 1 any failure, 2 harness error

This module is import-safe and host-free; run_ci_smoke.py is the entrypoint
that wires env + the mock around it.
"""
from __future__ import annotations

import importlib
import os
import subprocess
import sys
import time
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional
from xml.sax.saxutils import escape

from tests.ci import hermetic_manifest as M

REPO_ROOT = M.REPO_ROOT


# =============================================================================
# Result model
# =============================================================================

@dataclass
class Check:
    name: str
    passed: bool
    detail: str = ""
    duration_ms: int = 0


@dataclass
class Tier:
    id: int
    name: str
    checks: list[Check] = field(default_factory=list)
    skipped: bool = False
    skip_reason: str = ""
    warnings: list[str] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        # A skipped tier is neither pass nor fail on its own merits, but for
        # the ladder verdict we treat "ran and all green" as the only pass.
        return (not self.skipped) and all(c.passed for c in self.checks)

    @property
    def failures(self) -> list[Check]:
        return [c for c in self.checks if not c.passed]


# =============================================================================
# Tier 0 -- syntax sweep
# =============================================================================

def _tracked_py_files() -> list[str]:
    """Repo-relative .py paths. Prefer git (authoritative); fall back to walk."""
    try:
        out = subprocess.run(
            ["git", "ls-files", "*.py"],
            cwd=str(REPO_ROOT), capture_output=True, text=True, encoding="utf-8",
            timeout=30,
        )
        if out.returncode == 0 and out.stdout.strip():
            return [l.strip().lstrip("﻿") for l in out.stdout.splitlines() if l.strip()]
    except Exception:
        pass
    skip_dirs = {".git", ".venv", "ci-venv", "__pycache__", "node_modules"}
    files = []
    for p in REPO_ROOT.rglob("*.py"):
        if any(part in skip_dirs for part in p.parts):
            continue
        files.append(p.relative_to(REPO_ROOT).as_posix())
    return files


def tier0_syntax() -> Tier:
    import py_compile
    t = Tier(0, "syntax")
    quarantine = M.quarantined_syntax_files()
    files = _tracked_py_files()
    real_fails: list[tuple[str, str]] = []
    quarantined_still_broken: list[str] = []

    for rel in files:
        abspath = REPO_ROOT / rel
        if not abspath.exists():
            continue
        try:
            py_compile.compile(str(abspath), doraise=True)
        except py_compile.PyCompileError as e:
            msg = next((l for l in str(e).splitlines() if l.strip()), str(e))[:160]
            if rel in quarantine:
                quarantined_still_broken.append(rel)
            else:
                real_fails.append((rel, msg))
        except Exception as e:  # encoding errors etc.
            if rel in quarantine:
                quarantined_still_broken.append(rel)
            else:
                real_fails.append((rel, f"{type(e).__name__}: {str(e)[:140]}"))

    # One failing check per non-quarantined syntax error.
    for rel, msg in real_fails:
        t.checks.append(Check(f"syntax::{rel}", False, msg))

    # Summary check (always present so a green run has a visible testcase).
    t.checks.append(Check(
        f"syntax_sweep::{len(files)}_files",
        not real_fails,
        f"compiled={len(files)} new_failures={len(real_fails)} "
        f"quarantined={len(quarantine)}",
    ))

    # Quarantine hygiene: warn (not fail) on still-broken known debt, and
    # FAIL if a quarantine entry has actually been fixed (stale list = remove).
    for rel in quarantine:
        if rel in quarantined_still_broken:
            t.warnings.append(f"quarantined (still broken): {rel}")
    healed = quarantine - set(quarantined_still_broken)
    healed_present = [r for r in healed if (REPO_ROOT / r).exists()]
    if healed_present:
        t.checks.append(Check(
            "syntax_quarantine_stale",
            False,
            "these files now COMPILE; remove them from "
            f"tests/ci/syntax_quarantine.txt: {sorted(healed_present)}",
        ))
    return t


# =============================================================================
# Tier 1 -- import smoke
# =============================================================================

def tier1_import() -> Tier:
    t = Tier(1, "import")
    if str(REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(REPO_ROOT))
    for mod in M.IMPORTABLE_MODULES + M.present_app_modules():
        start = time.monotonic()
        try:
            importlib.import_module(mod)
            ok, detail = True, ""
        except Exception as e:
            ok = False
            detail = f"{type(e).__name__}: {e}\n" + traceback.format_exc(limit=3)
        t.checks.append(Check(
            f"import::{mod}", ok, detail,
            int((time.monotonic() - start) * 1000),
        ))
    return t


# =============================================================================
# Tier 2 -- schema contract (committed snapshots -> ephemeral DuckDB)
# =============================================================================

def tier2_schema() -> Tier:
    t = Tier(2, "schema")
    if str(REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(REPO_ROOT))
    try:
        import duckdb
        from zo_sentinel.schemas.loader import (
            load_duckdb_schema, load_mesh_memory_schema, compute_schema_hash,
        )
    except Exception as e:
        t.checks.append(Check("schema::loader_import", False,
                              f"{type(e).__name__}: {e}"))
        return t

    # --- committed DuckDB snapshot is loadable + well-formed ---------------
    start = time.monotonic()
    duck_snap = None
    try:
        duck_snap = load_duckdb_schema()
        ok = (duck_snap.engine == "duckdb"
              and len(duck_snap.tables()) > 0
              and bool(duck_snap.schema_hash))
        detail = f"engine={duck_snap.engine} tables={len(duck_snap.tables())}"
    except Exception as e:
        ok, detail = False, f"{type(e).__name__}: {e}"
    t.checks.append(Check("schema::duckdb_snapshot_loads", ok, detail,
                          int((time.monotonic() - start) * 1000)))

    # --- committed mesh_memory (sqlite) snapshot has the 5 canonical cols --
    start = time.monotonic()
    try:
        mem = load_mesh_memory_schema()
        names = set(mem.column_names("mesh_memory"))
        required = {"agent_id", "memory_type", "content", "importance", "created_at"}
        missing = required - names
        ok = mem.engine == "sqlite" and not missing
        detail = "" if ok else f"engine={mem.engine} missing={sorted(missing)}"
    except Exception as e:
        ok, detail = False, f"{type(e).__name__}: {e}"
    t.checks.append(Check("schema::mesh_memory_canonical_columns", ok, detail,
                          int((time.monotonic() - start) * 1000)))

    # --- compute_schema_hash is deterministic + order-independent ----------
    start = time.monotonic()
    try:
        rows = []
        if duck_snap is not None:
            for tbl in duck_snap.tables():
                for c in duck_snap.columns(tbl):
                    rows.append({"table_name": tbl, "column_name": c.name,
                                 "data_type": c.type})
        h1 = compute_schema_hash(rows)
        h2 = compute_schema_hash(list(reversed(rows)))
        ok = bool(h1) and h1 == h2
        detail = "" if ok else f"hash not order-independent: {h1!r} vs {h2!r}"
    except Exception as e:
        ok, detail = False, f"{type(e).__name__}: {e}"
    t.checks.append(Check("schema::hash_deterministic", ok, detail,
                          int((time.monotonic() - start) * 1000)))

    # --- the snapshot materializes into a fresh in-memory DuckDB -----------
    # Proves the committed column types are still valid DDL against the pinned
    # DuckDB version (catches a type rename that would break a real bootstrap).
    start = time.monotonic()
    try:
        con = duckdb.connect(":memory:")
        applied = 0
        if duck_snap is not None:
            for tbl in duck_snap.tables():
                coldefs = [f'"{c.name}" {c.type}' for c in duck_snap.columns(tbl)]
                if coldefs:
                    con.execute(
                        f'CREATE TABLE IF NOT EXISTS "{tbl}" ({", ".join(coldefs)})'
                    )
                    applied += 1
        created = {r[0] for r in con.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_schema='main'"
        ).fetchall()}
        con.close()
        ok = applied > 0 and len(created) == applied
        detail = f"created={len(created)}/{applied} tables"
    except Exception as e:
        ok, detail = False, f"{type(e).__name__}: {e}"
    t.checks.append(Check("schema::duckdb_materialize", ok, detail,
                          int((time.monotonic() - start) * 1000)))
    return t


# =============================================================================
# Tier 3 -- mock write_service contract
# =============================================================================

def tier3_service() -> Tier:
    t = Tier(3, "service")
    ws = os.environ.get("ZO_WRITE_SERVICE", "http://127.0.0.1:8772")
    try:
        import requests
    except Exception as e:
        t.checks.append(Check("service::requests_import", False, str(e)))
        return t

    # health
    start = time.monotonic()
    try:
        r = requests.get(ws + "/health", timeout=5)
        ok = r.status_code == 200 and r.json().get("status") == "healthy"
        detail = "" if ok else f"HTTP {r.status_code}: {r.text[:120]}"
    except Exception as e:
        ok, detail = False, f"{type(e).__name__}: {e}"
    t.checks.append(Check("service::health", ok, detail,
                          int((time.monotonic() - start) * 1000)))
    if not ok:
        # No point running the rest of the contract if health is down.
        return t

    table = M.MOCK_CONTRACT_TABLE
    marker = f"ci_smoke_{int(time.time())}"

    # write -> query round-trip
    start = time.monotonic()
    try:
        wr = requests.post(ws + "/write",
                           json={"table": table, "rows": [{"marker": marker, "v": 1}]},
                           timeout=5)
        w_ok = wr.status_code == 200 and wr.json().get("ok")
        qr = requests.post(ws + "/query",
                           json={"sql": f"SELECT * FROM {table} WHERE marker = '{marker}'"},
                           timeout=5)
        rows = qr.json().get("rows", []) if qr.status_code == 200 else []
        roundtrip_ok = w_ok and any(row.get("marker") == marker for row in rows)
        detail = "" if roundtrip_ok else f"write_ok={w_ok} rows_back={len(rows)}"
    except Exception as e:
        roundtrip_ok, detail = False, f"{type(e).__name__}: {e}"
    t.checks.append(Check("service::write_query_roundtrip", roundtrip_ok, detail,
                          int((time.monotonic() - start) * 1000)))

    # execute (DDL no-op contract)
    start = time.monotonic()
    try:
        er = requests.post(ws + "/execute",
                           json={"sql": "CREATE TABLE IF NOT EXISTS x (a INTEGER)"},
                           timeout=5)
        ex_ok = er.status_code == 200 and er.json().get("ok")
        detail = "" if ex_ok else f"HTTP {er.status_code}"
    except Exception as e:
        ex_ok, detail = False, f"{type(e).__name__}: {e}"
    t.checks.append(Check("service::execute", ex_ok, detail,
                          int((time.monotonic() - start) * 1000)))

    # Integration proof: the PARAMETRIZED gate_framework helpers must reach the
    # mock via $ZO_WRITE_SERVICE. This is what validates the env-parametrization
    # that makes the host gates CI-runnable in the first place.
    start = time.monotonic()
    try:
        if str(REPO_ROOT / "tests" / "gates") not in sys.path:
            sys.path.insert(0, str(REPO_ROOT / "tests" / "gates"))
        import gate_framework as gf
        fw_ok = gf.WS == ws  # proves the env override took effect
        gf_rows = gf.ws_query(f"SELECT * FROM {table} WHERE marker = '{marker}'")
        fw_ok = fw_ok and any(row.get("marker") == marker for row in gf_rows)
        detail = "" if fw_ok else f"gf.WS={gf.WS!r} rows={len(gf_rows)}"
    except Exception as e:
        fw_ok, detail = False, f"{type(e).__name__}: {e}"
    t.checks.append(Check("service::gate_framework_env_binding", fw_ok, detail,
                          int((time.monotonic() - start) * 1000)))
    return t


# =============================================================================
# Ladder runner (recursive short-circuit) + reporting
# =============================================================================

LADDER: list[tuple[int, str, Callable[[], Tier]]] = [
    (0, "syntax", tier0_syntax),
    (1, "import", tier1_import),
    (2, "schema", tier2_schema),
    (3, "service", tier3_service),
]


def run_ladder(stop_on_fail: bool = True) -> list[Tier]:
    results: list[Tier] = []
    broken_at: Optional[int] = None
    for tid, name, fn in LADDER:
        if broken_at is not None and stop_on_fail:
            tier = Tier(tid, name, skipped=True,
                        skip_reason=f"short-circuited by tier {broken_at} failure")
            results.append(tier)
            continue
        print(f"\n=== Tier {tid}: {name} ===")
        try:
            tier = fn()
        except Exception as e:
            tier = Tier(tid, name)
            tier.checks.append(Check(f"{name}::harness_error", False,
                                     f"{type(e).__name__}: {e}\n{traceback.format_exc(limit=4)}"))
        results.append(tier)
        for c in tier.checks:
            print(f"  [{'PASS' if c.passed else 'FAIL'}] {c.name}"
                  + (f"  -- {c.detail.splitlines()[0]}" if c.detail else ""))
        for w in tier.warnings:
            print(f"  [warn] {w}")
        if not tier.passed:
            broken_at = tid
    return results


def write_junit(results: list[Tier], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    total = sum(len(t.checks) for t in results)
    failures = sum(len(t.failures) for t in results)
    skipped = sum(1 for t in results if t.skipped)
    lines = [
        '<?xml version="1.0" encoding="utf-8"?>',
        f'<testsuites name="ci_smoke_ladder" tests="{total}" '
        f'failures="{failures}" skipped="{skipped}">',
    ]
    for t in results:
        suite = f"tier{t.id}_{t.name}"
        lines.append(
            f'  <testsuite name="{escape(suite)}" tests="{len(t.checks)}" '
            f'failures="{len(t.failures)}" skipped="{1 if t.skipped else 0}">'
        )
        if t.skipped:
            lines.append(
                f'    <testcase classname="{escape(suite)}" name="(tier skipped)">'
                f'<skipped message="{escape(t.skip_reason)}"/></testcase>'
            )
        for c in t.checks:
            tc = (f'    <testcase classname="{escape(suite)}" '
                  f'name="{escape(c.name)}" time="{c.duration_ms/1000:.3f}">')
            if c.passed:
                lines.append(tc + "</testcase>")
            else:
                first = (c.detail.splitlines()[0] if c.detail else "check failed")
                lines.append(tc)
                lines.append(f'      <failure message="{escape(first)}">'
                             f'{escape(c.detail)}</failure>')
                lines.append("    </testcase>")
        lines.append("  </testsuite>")
    lines.append("</testsuites>")
    path.write_text("\n".join(lines), encoding="utf-8")


def summarize(results: list[Tier]) -> int:
    print("\n" + "=" * 60)
    print("CI SMOKE LADDER SUMMARY")
    print("=" * 60)
    overall_ok = True
    for t in results:
        if t.skipped:
            status = "SKIP"
        elif t.passed:
            status = "PASS"
        else:
            status = "FAIL"
            overall_ok = False
        nfail = len(t.failures)
        extra = f" ({nfail} failed)" if nfail else ""
        print(f"  Tier {t.id} {t.name:<9} {status}{extra}")
    print("=" * 60)
    print("RESULT:", "PASS" if overall_ok else "FAIL")
    return 0 if overall_ok else 1
