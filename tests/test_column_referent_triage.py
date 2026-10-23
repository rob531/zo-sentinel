"""Coverage for tools/column_referent_triage.py.

This runs the tool's OWN self-test, which drives each liveness control at both
poles. It is not a new gate on the product: the tool is report-only and wired
into no workflow. It is the existing `pytest` context covering new code.

The controls exist because the hand-done version of this classification got
control 1 wrong (matched a running process to a repo file by basename, and so
read `pattern_learner` as live when the running `signal_analyser.py` is a
different file from the `sentinel/signal_analyser.py` that imports it).
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / "tools" / "column_referent_triage.py"


def _load():
    spec = importlib.util.spec_from_file_location("column_referent_triage", TOOL)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def test_tool_exists():
    assert TOOL.exists(), TOOL


def test_self_test_passes():
    assert _load()._self_test() == 0


def test_basename_decoy_is_not_a_runtime_match():
    """The FU-152 shape: two files, one basename, only one running."""
    m = _load()
    rt = m.runtime_paths("python3 /home/workspace/zo_sentinel/signal_analyser.py\n")
    assert m.runtime_matches("signal_analyser.py", rt) != []
    assert m.runtime_matches("sentinel/signal_analyser.py", rt) == []


def test_bare_argv_name_resolves_nothing():
    m = _load()
    assert m.runtime_paths("python3 signal_analyser.py\n") == []


def test_missing_columns_section_is_unknown_not_zero():
    m = _load()
    with pytest.raises(ValueError):
        m.triage({"columns": {}}, ROOT, "")


def test_absent_runtime_oracle_is_labelled_unknown():
    m = _load()
    t = m.triage({"columns": {"missing": {}, "checked": 0}}, ROOT, "")
    assert "NOT SUPPLIED" in t["basis"]["runtime_oracle"]


def test_spine_mounts_are_parsed_without_importing_the_app():
    m = _load()
    mounts = m.spine_import_paths(ROOT)
    assert mounts, "app/_spine_generated.py yielded no SPINE_MOUNTS import_paths"
    a = sorted(mounts)[0]
    assert m.classify_file(f"{a}.py", mounts, []) == "MOUNTED"
    assert m.classify_file("definitely_not_a_service_zzz.py", mounts, []) == "SEDIMENT"


def test_live_and_sediment_partition_the_missing_set():
    """Whatever the counts are, the two halves must sum to the whole."""
    m = _load()
    report = {
        "columns": {
            "checked": 3,
            "missing": {
                "t.a": ["approval_workflow.py:270"],
                "t.b": ["some_unmounted_sediment_module_zzz.py:12"],
                "t.c": ["quarantine/whatever_zzz.py:3"],
            },
        }
    }
    t = m.triage(report, ROOT, "python3 /home/workspace/zo_sentinel/approval_workflow.py\n")
    assert t["live_column_count"] + t["sediment_column_count"] == 3
    assert t["live_columns"] == ["t.a"]


def test_approval_workflow_audit_names_only_real_columns():
    """The /api/audit SELECT must not name mcp_submissions columns that do not
    exist on any plane. Measured 2026-09-29: the real columns are
    submission_id, server_id, mcp_name, url, description, requested_by,
    business_purpose, environment, submitted_at, status."""
    src = (ROOT / "approval_workflow.py").read_text(encoding="utf-8")
    body = src.split('@app.get("/api/audit")', 1)[1].split("@app.")[0]
    # The docstring records the OLD names deliberately, so assert on the SQL
    # only -- everything after the docstring's closing quotes.
    sql = body.split('"""')[2] if body.count('"""') >= 2 else body
    assert "s.mcp_identifier" not in sql
    assert "s.requester_team" not in sql
    assert "s.mcp_name AS mcp_identifier" in sql
    assert "NULL AS requester_team" in sql
