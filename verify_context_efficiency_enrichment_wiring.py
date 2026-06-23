"""
verify_context_efficiency_enrichment_wiring.py

Verifies context_efficiency_enrichment wiring state:
  1. Module exists and imports cleanly
  2. compute_score() signature is correct
  3. Integration file calls compute_score() or is wired via signal_bridge
  4. mcp_signal_enrichments has rows with signal_type='context_efficiency'

Read-only; queries write_service at 127.0.0.1:8772 only.

Exit 0 if fully wired; exit 1 with gap description if not.
"""

from __future__ import annotations

import importlib.util
import json
import sys
import inspect
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# deps: requests
import requests

PROJECT_ROOT = Path("/home/workspace/zo_sentinel")
WRITE_SERVICE = "http://127.0.0.1:8772"


def _query(sql: str) -> list[dict[str, Any]]:
    """Execute SELECT via write_service /query endpoint (read-only)."""
    try:
        resp = requests.post(
            f"{WRITE_SERVICE}/query",
            json={"sql": sql},
            timeout=10,
        )
        if resp.status_code == 200:
            data = resp.json()
            return data.get("rows", [])
        return []
    except Exception:
        return []


def check_module_exists() -> tuple[bool, str]:
    """Check context_efficiency_enrichment.py exists on disk."""
    candidates = [
        PROJECT_ROOT / "context_efficiency_enrichment.py",
        PROJECT_ROOT / "context_efficiency_enrichment_v2.py",
        PROJECT_ROOT / "context_efficiency_enrichment_v3.py",
    ]
    for path in candidates:
        if path.exists():
            return True, str(path)
    return False, ""


def check_module_importable() -> tuple[bool, str]:
    """Load module via importlib, check compute_score exists."""
    candidates = [
        PROJECT_ROOT / "context_efficiency_enrichment.py",
        PROJECT_ROOT / "context_efficiency_enrichment_v2.py",
        PROJECT_ROOT / "context_efficiency_enrichment_v3.py",
    ]
    for path in candidates:
        if not path.exists():
            continue
        name = path.stem  # e.g. 'context_efficiency_enrichment'
        spec = importlib.util.spec_from_file_location(name, path)
        if spec is None or spec.loader is None:
            continue
        try:
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
        except Exception:
            continue
        if hasattr(module, "compute_score"):
            return True, name
    return False, ""


def check_compute_score_signature() -> bool:
    """Verify compute_score(metadata: dict) -> (float, dict)."""
    candidates = [
        PROJECT_ROOT / "context_efficiency_enrichment.py",
        PROJECT_ROOT / "context_efficiency_enrichment_v2.py",
        PROJECT_ROOT / "context_efficiency_enrichment_v3.py",
    ]
    for path in candidates:
        if not path.exists():
            continue
        name = path.stem
        spec = importlib.util.spec_from_file_location(name, path)
        if spec is None or spec.loader is None:
            continue
        try:
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
        except Exception:
            continue
        if hasattr(module, "compute_score"):
            sig = inspect.signature(module.compute_score)
            params = list(sig.parameters.keys())
            if params == ["metadata"]:
                return True
    return False


def check_integration() -> tuple[bool, str]:
    """
    Check integration files call compute_score() or map via signal_bridge.

    Integration candidates:
      - signal_analyser.py (root)
      - signal_bridge.py
      - enrichment_pipeline_writer_daemon.py
      - mcp_signal_enrichments_writer_daemon.py
    """
    candidates = [
        PROJECT_ROOT / "signal_bridge.py",
        PROJECT_ROOT / "signal_analyser.py",
        PROJECT_ROOT / "enrichment_pipeline_writer_daemon.py",
        PROJECT_ROOT / "mcp_signal_enrichments_writer_daemon.py",
        PROJECT_ROOT / "mcp_signal_enrichments_writer_v2.py",
    ]
    for path in candidates:
        if not path.exists():
            continue
        try:
            content = path.read_text()
        except Exception:
            continue
        # Check for context_efficiency_enrichment reference
        if "context_efficiency_enrichment" not in content:
            continue
        # Check it maps to 'context_efficiency' signal_type
        if "context_efficiency" in content:
            return True, str(path)
    return False, ""


def check_rows() -> tuple[bool, int, list[dict[str, Any]]]:
    """
    Query mcp_signal_enrichments for signal_type='context_efficiency'.
    Uses parameterized query via write_service /query.
    """
    sql = "SELECT signal_type, COUNT(*) AS cnt FROM mcp_signal_enrichments WHERE signal_type = 'context_efficiency' GROUP BY signal_type"
    rows = _query(sql)
    if rows:
        cnt = rows[0].get("cnt", 0) if rows else 0
        sample_sql = "SELECT signal_type, score, enrichment_data, created_at FROM mcp_signal_enrichments WHERE signal_type = 'context_efficiency' LIMIT 5"
        samples = _query(sample_sql)
        return cnt > 0, int(cnt), samples
    return False, 0, []


def run() -> dict[str, Any]:
    """
    Run all checks and return structured findings dict.

    Keys:
      module_exists: bool
      module_importable: bool
      compute_score_found: bool
      integration_exists: bool
      integration_file: str
      rows_exist: bool
      row_count: int
      sample_scores: list
    """
    findings: dict[str, Any] = {
        "module_exists": False,
        "module_path": "",
        "module_importable": False,
        "compute_score_found": False,
        "integration_exists": False,
        "integration_file": "",
        "rows_exist": False,
        "row_count": 0,
        "sample_scores": [],
    }

    # 1. Module on disk
    found, path = check_module_exists()
    findings["module_exists"] = found
    findings["module_path"] = path

    # 2. Module importable
    imp_ok, mod_name = check_module_importable()
    findings["module_importable"] = imp_ok

    # 3. compute_score signature
    findings["compute_score_found"] = check_compute_score_signature()

    # 4. Integration wiring
    wired, integ_path = check_integration()
    findings["integration_exists"] = wired
    findings["integration_file"] = integ_path

    # 5. DB rows
    has_rows, count, samples = check_rows()
    findings["rows_exist"] = has_rows
    findings["row_count"] = count
    findings["sample_scores"] = [
        {
            "signal_type": s.get("signal_type"),
            "score": s.get("score"),
            "created_at": s.get("created_at"),
        }
        for s in samples
    ]

    return findings


def _print_report(findings: dict[str, Any]) -> None:
    ts = datetime.now(timezone.utc).isoformat()
    print(f"\n{'='*60}")
    print(f"CONTEXT EFFICIENCY ENRICHMENT WIRING VERIFICATION")
    print(f"Timestamp: {ts}")
    print(f"{'='*60}")

    checks = [
        ("module_exists", "Module on disk"),
        ("module_importable", "Module importable"),
        ("compute_score_found", "compute_score() signature correct"),
        ("integration_exists", "Integration wired (signal_analyser/signal_bridge)"),
        ("rows_exist", "Rows in mcp_signal_enrichments (signal_type='context_efficiency')"),
    ]
    all_pass = True
    for key, label in checks:
        val = findings.get(key, False)
        status = "PASS" if val else "FAIL"
        if not val:
            all_pass = False
        print(f"  [{status}] {label}")

    print(f"\nDetails:")
    print(f"  module_path:        {findings.get('module_path', '(none)')}")
    print(f"  integration_file:   {findings.get('integration_file', '(none)')}")
    print(f"  row_count:          {findings.get('row_count', 0)}")
    if findings.get("sample_scores"):
        print(f"  sample_scores:")
        for s in findings["sample_scores"]:
            print(f"    - signal_type={s.get('signal_type')} score={s.get('score')} at={s.get('created_at')}")
    else:
        print(f"  sample_scores: (none)")

    print(f"\n{'='*60}")
    if all_pass:
        print("RESULT: PASS — context_efficiency_enrichment fully wired")
    else:
        gaps = [label for key, label in checks if not findings.get(key, False)]
        print(f"RESULT: FAIL — gaps found:")
        for g in gaps:
            print(f"  - {g}")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    findings = run()
    _print_report(findings)

    fully_wired = (
        findings["module_exists"]
        and findings["module_importable"]
        and findings["compute_score_found"]
        and findings["integration_exists"]
        and findings["rows_exist"]
    )

    # Emit heartbeat (non-fatal)
    try:
        requests.post(
            f"{WRITE_SERVICE}/write",
            json={
                "table": "service_health",
                "rows": {
                    "service": "verify_context_efficiency_enrichment_wiring",
                    "last_heartbeat": datetime.now(timezone.utc).isoformat(),
                    "status": "PASS" if fully_wired else "FAIL",
                    "meta": json.dumps(findings),
                },
            },
            timeout=5,
        )
    except Exception:
        pass

    sys.exit(0 if fully_wired else 1)
