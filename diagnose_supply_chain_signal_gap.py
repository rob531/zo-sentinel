#!/usr/bin/env python3
"""
diagnose_supply_chain_signal_gap.py

Diagnostic utility to investigate why the supply_chain signal has zero rows in
mcp_signal_scores despite MCPs in mcp_server_registry.  Identifies which
signal_analyser or enricher module is responsible for supply_chain and why it is
not emitting rows.

INTERFACE:
    if __name__ == '__main__':
        run()   # prints findings to stdout; exits 0 on diagnostic complete,
                # exits 1 if a critical gap is found
"""

import os
import sys
import json
import requests
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, List, Optional

# deps: requests

WRITE_SERVICE = "http://127.0.0.1:8772"
HTTP_TIMEOUT = 10

# Canonical paths to check for the supply_chain signal source
CANDIDATE_FILES = {
    "supply_chain_signal_enricher": "/home/workspace/zo_sentinel/supply_chain_signal_enricher.py",
    "signal_analyser": "/home/workspace/zo_sentinel/signal_analyser.py",
    "signal_analyser_v2": "/home/workspace/zo_sentinel/signal_analyser_v2.py",
    "signal_analyser_v3": "/home/workspace/zo_sentinel/signal_analyser_v3.py",
    "signal_analyser_v4": "/home/workspace/zo_sentinel/signal_analyser_v4.py",
    "discrimination_enrichers": "/home/workspace/zo_sentinel/enrichers/discrimination_enrichers.py",
}


# -------------------------------------------------------------------------- #
# write_service helpers
# -------------------------------------------------------------------------- #

def ws_query(sql: str) -> List[Dict[str, Any]]:
    """Execute a SELECT via write_service /query."""
    payload = {"sql": sql}
    for attempt in range(3):
        try:
            resp = requests.post(
                f"{WRITE_SERVICE}/query",
                json=payload,
                timeout=HTTP_TIMEOUT,
            )
            if resp.status_code == 200:
                data = resp.json()
                return data.get("rows", [])
            # Non-200: log and retry
            sys.stderr.write(
                f"[ws_query] attempt {attempt+1} HTTP {resp.status_code}: {resp.text[:200]}\n"
            )
        except requests.exceptions.RequestException as exc:
            sys.stderr.write(f"[ws_query] attempt {attempt+1} exception: {exc}\n")
        import time
        time.sleep(1)
    return []


def ws_execute(sql: str) -> bool:
    """Execute DDL/DML via write_service /execute."""
    payload = {"sql": sql}
    for attempt in range(3):
        try:
            resp = requests.post(
                f"{WRITE_SERVICE}/execute",
                json=payload,
                timeout=HTTP_TIMEOUT,
            )
            if resp.status_code in (200, 201):
                return True
            sys.stderr.write(
                f"[ws_execute] attempt {attempt+1} HTTP {resp.status_code}: {resp.text[:200]}\n"
            )
        except requests.exceptions.RequestException as exc:
            sys.stderr.write(f"[ws_execute] attempt {attempt+1} exception: {exc}\n")
        import time
        time.sleep(1)
    return False


# -------------------------------------------------------------------------- #
# File-system helpers
# -------------------------------------------------------------------------- #

def file_info(path: str) -> Optional[Dict[str, Any]]:
    """Return {exists, mtime, size} for path or None."""
    p = Path(path)
    if not p.is_file():
        return None
    stat = p.stat()
    return {
        "exists": True,
        "mtime": datetime.fromtimestamp(stat.st_mtime).isoformat(),
        "size_bytes": stat.st_size,
    }


def find_supply_chain_function(filepath: str) -> Optional[str]:
    """
    Grep the source of filepath for the function/class that emits the
    'supply_chain' signal.  Returns the identifier name or None.
    """
    candidates = [
        "compute_supply_chain_score",
        "supply_chain_score",
        "compute_score",
        "SupplyChainEnricher",
        "SupplyChainEnricherV2",
        "SupplyChainEnricherV3",
    ]
    try:
        with open(filepath, "r", encoding="utf-8", errors="replace") as fh:
            content = fh.read()
    except Exception as exc:
        return None

    for name in candidates:
        # Simple line-level scan – if the identifier appears in a def/class line
        for line in content.splitlines():
            stripped = line.strip()
            if stripped.startswith("def ") and name in stripped:
                return name
            if stripped.startswith("class ") and name in stripped:
                return name
    return None


def find_write_target(filepath: str) -> Optional[str]:
    """
    Determine whether filepath writes to mcp_signal_scores or
    mcp_signal_enrichments (or neither / both) by scanning for known write
    patterns.  Returns one of: 'mcp_signal_scores', 'mcp_signal_enrichments',
    'both', 'none'.
    """
    try:
        with open(filepath, "r", encoding="utf-8", errors="replace") as fh:
            content = fh.read()
    except Exception:
        return None

    scores = "mcp_signal_scores" in content
    enrichments = "mcp_signal_enrichments" in content

    if scores and enrichments:
        return "both"
    if scores:
        return "mcp_signal_scores"
    if enrichments:
        return "mcp_signal_enrichments"
    return "none"


# -------------------------------------------------------------------------- #
# Core diagnostic logic
# -------------------------------------------------------------------------- #

def get_signal_counts() -> Dict[str, int]:
    """Return {signal_name: row_count} for mcp_signal_scores."""
    rows = ws_query(
        "SELECT signal_name, COUNT(*) AS cnt "
        "FROM mcp_signal_scores "
        "GROUP BY signal_name "
        "ORDER BY cnt DESC"
    )
    return {r["signal_name"]: r["cnt"] for r in rows}


def get_registry_count() -> int:
    """Return total row count in mcp_server_registry."""
    rows = ws_query("SELECT COUNT(*) AS cnt FROM mcp_server_registry")
    return rows[0]["cnt"] if rows else 0


def get_supply_chain_row_count() -> int:
    """Return row count for signal_name = 'supply_chain' in mcp_signal_scores."""
    rows = ws_query(
        "SELECT COUNT(*) AS cnt FROM mcp_signal_scores "
        "WHERE signal_name = 'supply_chain'"
    )
    return rows[0]["cnt"] if rows else 0


def locate_supply_chain_source() -> Dict[str, Any]:
    """
    Search CANDIDATE_FILES for the module that owns the supply_chain signal.
    Returns a dict describing the best match.
    """
    best = {"file": None, "exists": False, "mtime": None, "function": None, "write_target": None}

    for name, path in CANDIDATE_FILES.items():
        finfo = file_info(path)
        if finfo is None:
            continue

        func = find_supply_chain_function(path)
        wt = find_write_target(path)

        # Prefer a file that has the supply_chain function AND a known write target
        if func and wt in ("mcp_signal_scores", "both"):
            return {
                "file": path,
                "label": name,
                "exists": True,
                "mtime": finfo["mtime"],
                "size_bytes": finfo["size_bytes"],
                "function": func,
                "write_target": wt,
            }

        # Fallback: keep the best hit so far
        if best["file"] is None or (func and best["function"] is None):
            best = {
                "file": path,
                "label": name,
                "exists": True,
                "mtime": finfo["mtime"],
                "size_bytes": finfo["size_bytes"],
                "function": func,
                "write_target": wt,
            }

    return best


# -------------------------------------------------------------------------- #
# Report
# -------------------------------------------------------------------------- #

def run() -> int:
    print("=" * 70)
    print("SUPPLY CHAIN SIGNAL GAP DIAGNOSTIC")
    print(f"Started: {datetime.utcnow().isoformat()} UTC")
    print("=" * 70)

    # 1. Row counts per signal_type in mcp_signal_scores
    print("\n[1] SIGNAL ROW COUNTS (mcp_signal_scores)")
    print("-" * 70)
    signal_counts = get_signal_counts()
    if signal_counts:
        for sig, cnt in signal_counts.items():
            flag = " <-- ZERO (gap)" if sig == "supply_chain" and cnt == 0 else ""
            sig_display = sig if sig else "(NULL)"
            cnt_display = cnt if cnt is not None else 0
            print(f"  {sig_display:35s}: {cnt_display:>6,} rows{flag}")
    else:
        print("  (table empty or unreachable)")

    # 2. Which signal is missing / sparse
    print("\n[2] GAP ANALYSIS")
    print("-" * 70)
    registry_count = get_registry_count()
    print(f"  mcp_server_registry total rows : {registry_count:,}")

    supply_chain_rows = get_supply_chain_row_count()
    print(f"  supply_chain rows in scores   : {supply_chain_rows:,}")

    if supply_chain_rows == 0:
        print("  STATUS : supply_chain has ZERO rows – CRITICAL GAP")
        gap_found = True
    elif supply_chain_rows < registry_count * 0.5:
        print(f"  STATUS : supply_chain is sparse ({supply_chain_rows}/{registry_count})")
        gap_found = True
    else:
        print("  STATUS : supply_chain appears populated")
        gap_found = False

    # 3. Responsible module / function
    print("\n[3] RESPONSIBLE MODULE")
    print("-" * 70)
    source = locate_supply_chain_source()
    if source["file"]:
        print(f"  File  : {source['file']}")
        print(f"  Label : {source['label']}")
        print(f"  Size  : {source.get('size_bytes', 'N/A'):,} bytes")
        print(f"  MTime : {source.get('mtime', 'N/A')}")
        if source["function"]:
            print(f"  Func  : {source['function']}()")
        else:
            print("  Func  : (not detected – manual review required)")
    else:
        print("  No candidate source file found on disk.")
        source = {"function": None, "write_target": None}

    # 4. File existence / mtime already shown above; add explicit line
    print("\n[4] FILE ON DISK CHECK")
    print("-" * 70)
    if source["file"]:
        finfo = file_info(source["file"])
        if finfo:
            print(f"  Exists : YES")
            print(f"  MTime   : {finfo['mtime']}")
            print(f"  Size    : {finfo['size_bytes']:,} bytes")
        else:
            print("  Exists : NO (file not found)")
    else:
        print("  Exists : NO (no candidate file identified)")

    # 5. Write target (mcp_signal_scores vs mcp_signal_enrichments)
    print("\n[5] WRITE TARGET TABLE")
    print("-" * 70)
    wt = source.get("write_target")
    if wt == "mcp_signal_scores":
        print("  Module writes directly to mcp_signal_scores.")
        print("  Gap reason: the module is either not being called, its input")
        print("  metadata is empty, or the rows are being dropped upstream.")
    elif wt == "mcp_signal_enrichments":
        print("  Module writes to mcp_signal_enrichments (NOT mcp_signal_scores).")
        print("  Gap reason: enrichments may not be flowing into mcp_signal_scores.")
        print("  Check the enrichment bridge / signal_analyser wiring.")
    elif wt == "both":
        print("  Module writes to both tables.")
        print("  Gap reason: rows may exist in enrichments but not scores.")
        print("  Verify the bridge from enrichments → mcp_signal_scores.")
    elif wt == "none":
        print("  Module does NOT write to either canonical score table.")
        print("  Gap reason: the module emits via compute_score() but the caller")
        print("  (signal_analyser) is responsible for persisting the result.")
        # Follow up: check signal_analyser
        sa_source = locate_supply_chain_source()
        if sa_source.get("write_target") == "mcp_signal_scores":
            print(f"  NOTE: signal_analyser ({sa_source['file']}) writes to scores.")
            print("  Ensure signal_analyser calls supply_chain enricher.")
    else:
        print(f"  Write target : unknown (wt={wt})")

    # Summary
    print("\n" + "=" * 70)
    print("SUMMARY")
    print("=" * 70)
    if gap_found:
        if source.get("function"):
            print(f"  supply_chain signal is MISSING from mcp_signal_scores.")
            print(f"  Responsible module: {source['file']}")
            print(f"  Responsible function: {source['function']}()")
            print(f"  Write target: {source.get('write_target', 'unknown')}")
            print(f"  Action: inspect why {source['function']}() is not being called,")
            print(f"          or why its output is not being persisted to scores.")
        else:
            print("  supply_chain signal is MISSING; no responsible module identified.")
            print("  Check supply_chain_signal_enricher.py and signal_analyser.py.")
    else:
        print("  No critical supply_chain gap detected.")

    print("=" * 70)
    return 1 if gap_found else 0


# -------------------------------------------------------------------------- #
# Self-smoke test (run without write_service dependency)
# -------------------------------------------------------------------------- #

def _smoke_local() -> None:
    """Verify helpers behave correctly without any I/O."""
    import tempfile

    # Create a fake module file
    fake_content = '''
def compute_supply_chain_score(server):
    return {"signal_name": "supply_chain", "score": 75.0}
'''
    with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as fh:
        fh.write(fake_content)
        fake_path = fh.name

    try:
        fi = file_info(fake_path)
        assert fi is not None and fi["exists"], "file_info failed"
        assert "mtime" in fi, "mtime missing"
        assert fi["size_bytes"] > 0, "size_bytes missing"

        func = find_supply_chain_function(fake_path)
        assert func == "compute_supply_chain_score", f"unexpected func: {func}"

        wt = find_write_target(fake_path)
        assert wt == "none", f"unexpected write target: {wt}"

        # Test with content that mentions mcp_signal_scores
        with open(fake_path, "w") as fh:
            fh.write("mcp_signal_scores\nws_write('mcp_signal_scores'")
        wt2 = find_write_target(fake_path)
        assert wt2 == "mcp_signal_scores", f"unexpected: {wt2}"

        print("[_smoke_local] PASS", file=sys.stderr)
    finally:
        os.unlink(fake_path)


# -------------------------------------------------------------------------- #

if __name__ == "__main__":
    if "--smoke" in sys.argv:
        _smoke_local()
        sys.exit(0)
    sys.exit(run())
