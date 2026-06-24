#!/usr/bin/env python3
# deps: requests
"""
verify_all_enrichments_wired.py

Utility script that inspects the current codebase to ensure that all built
enrichment modules are wired into ``signal_analyser.py``.

It performs three checks:

1. **Import Presence** – verifies that each enrichment module is imported
   in ``signal_analyser.py``.
2. **Compute Score Calls** – ensures that the module's ``compute_score``
   function is referenced (a simple heuristic based on a textual search).
3. **DB Row Count** – queries the ``mcp_signal_enrichments`` table and
   reports the total row count.

If any enrichment is not wired, the script prints a minimal *wiring patch*
that can be inserted into ``signal_analyser.py`` to import the missing module
and call its ``compute_score`` function for each server processed.

The script is pure (no side‑effects other than the optional stdout) and can
be run directly:

    python3 verify_all_enrichments_wired.py
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import List, Tuple

import requests

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
SIGNAL_ANALYSER_PATH = Path(__file__).parent / "signal_analyser.py"
ENRICHMENT_MODULES = [
    "directory_presence_signal_enrichment",
    "operator_identity_signal",
    "data_residency_signal",
    "endpoint_trust_signal",
]
WRITE_SERVICE_URL = "http://127.0.0.1:8772"
QUERY_URL = f"{WRITE_SERVICE_URL}/query"
HTTP_TIMEOUT = 10

# ---------------------------------------------------------------------------
# Helper utilities
# ---------------------------------------------------------------------------
def _read_source(path: Path) -> str:
    return path.read_text(encoding="utf-8")

def _module_imported(source: str, module_name: str) -> bool:
    # Simple regex to catch "import <module>" or "from <module> import …"
    pattern = rf"(?:import\s+{re.escape(module_name)}\b|from\s+{re.escape(module_name)}\b)"
    return re.search(pattern, source) is not None

def _calls_compute_score(source: str, module_name: str) -> bool:
    # Look for "<module>.compute_score" or "compute_score" after an import.
    # This is a heuristic – we just search for the literal string.
    return re.search(rf"{re.escape(module_name)}\.compute_score", source) is not None

def _query_enrichments_row_count() -> int:
    payload = {"sql": "SELECT COUNT(*) AS cnt FROM mcp_signal_enrichments"}
    try:
        resp = requests.post(QUERY_URL, json=payload, timeout=HTTP_TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
        rows = data.get("rows", [])
        if rows and isinstance(rows, list):
            return int(rows[0].get("cnt", 0))
    except Exception as e:
        print(f"[ERROR] Failed to query mcp_signal_enrichments: {e}", file=sys.stderr)
    return -1

def _generate_wiring_patch(missing_modules: List[str]) -> str:
    """Return a minimal patch snippet that can be inserted into ``signal_analyser.py``.

    The patch adds import statements and a call to ``compute_score`` for each
    missing enrichment inside the ``process_server`` function.
    """
    if not missing_modules:
        return ""  # nothing to patch
    imports = "\n".join([f"import {m}" for m in missing_modules])
    # Build a call block – we assume ``server`` variable is in scope.
    calls = "\n        ".join([f"signals.append({m}.compute_score(server))" for m in missing_modules])
    patch = (
        "# ---- AUTO‑GENERATED ENRICHMENT WIRING PATCH ----\n"
        f"{imports}\n\n"
        "# Insert the following lines inside the ``process_server`` function, after the existing signal calls:\n"
        f"        {calls}\n"
        "# -------------------------------------------------"
    )
    return patch

# ---------------------------------------------------------------------------
# Main verification logic
# ---------------------------------------------------------------------------
def verify() -> Tuple[bool, str]:
    """Run the verification checks.

    Returns a tuple ``(all_wired, report)`` where ``all_wired`` is ``True`` if
    every enrichment module is imported and referenced, and ``report`` is a
    human‑readable multi‑line string describing the findings.
    """
    if not SIGNAL_ANALYSER_PATH.is_file():
        return False, f"signal_analyser.py not found at {SIGNAL_ANALYSER_PATH}"

    source = _read_source(SIGNAL_ANALYSER_PATH)
    missing_modules: List[str] = []
    missing_calls: List[str] = []

    for mod in ENRICHMENT_MODULES:
        imported = _module_imported(source, mod)
        called = _calls_compute_score(source, mod)
        if not imported:
            missing_modules.append(mod)
        elif not called:
            # Imported but never used – treat as missing call.
            missing_calls.append(mod)

    # DB row count check – we only report the number; the logic for a patch
    # is outside the scope of this script.
    row_count = _query_enrichments_row_count()
    db_report = f"mcp_signal_enrichments rows: {row_count if row_count >= 0 else 'unavailable'}"

    # Build report
    lines = ["=== Enrichment Wiring Verification Report ===", db_report, ""]
    if not missing_modules and not missing_calls:
        lines.append("All enrichment modules are imported and compute_score is referenced.")
        all_wired = True
    else:
        all_wired = False
        if missing_modules:
            lines.append("Missing imports for modules:")
            for m in missing_modules:
                lines.append(f"  - {m}")
        if missing_calls:
            lines.append("Imported but compute_score not called for modules:")
            for m in missing_calls:
                lines.append(f"  - {m}")
        # Suggest patch for missing imports/calls
        patch = _generate_wiring_patch(missing_modules + missing_calls)
        if patch:
            lines.append("")
            lines.append("Suggested wiring patch (copy‑paste into signal_analyser.py):")
            lines.append(patch)
    report = "\n".join(lines)
    return all_wired, report

# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    success, msg = verify()
    print(msg)
    sys.exit(0 if success else 1)
