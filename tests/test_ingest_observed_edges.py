"""test_ingest_observed_edges.py -- the "observed / is" half of the app-model graph.

Hermetic: feeds a fake app_shell ndjson (proxied UI->endpoint requests) through
the tool as a subprocess and asserts the emitted SQL -- the observed_calls edges,
the id-normalised endpoints, and the two self-correcting gap views. The tool
resolves its input/output paths from argv at import time, so it is exercised as a
subprocess rather than imported; that is still a real invocation of the live code.

This test is the tool's only CI subscriber: dark_tools.py drops tests/ from its
caller scan by design, so the tool reads DARK there and carries an EXPECTED_DARK
entry pointing back here. If this file stops invoking the tool, that exemption is
stale.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

_TOOL = Path(__file__).resolve().parent.parent / "tools" / "ingest_observed_edges.py"


def _run(ndjson_path, out_path):
    return subprocess.run(
        [sys.executable, str(_TOOL), str(ndjson_path), str(out_path)],
        capture_output=True, text=True, timeout=60,
    )


def test_emits_observed_edges_and_gap_views(tmp_path):
    src = tmp_path / "observed.ndjson"
    rows = [
        # id-ish segment (12345) must collapse to /{id}
        {"method": "GET", "path": "/api/signals/12345", "port": 8001, "prefix": "/api", "ui": "dashboard.html"},
        {"method": "POST", "path": "/api/score", "port": 8001, "prefix": "/api", "ui": "dashboard.html"},
        {"method": "GET", "path": "/health", "port": 8001, "prefix": "", "ui": ""},        # no UI edge (empty ui)
        {"method": "GET", "path": "/x", "port": 8001, "prefix": "", "ui": "widget.txt"},    # no UI edge (not .html)
    ]
    src.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    out = tmp_path / "app_graph_observed.sql"

    r = _run(src, out)
    assert r.returncode == 0, r.stderr
    sql = out.read_text(encoding="utf-8")

    # id-ish path segment is normalised
    assert "ep:GET:/api/signals/{id}" in sql
    # the .html UI pages produce observed_calls edges; the empty/non-html ones do not
    assert "'ui:dashboard.html'" in sql and "'observed_calls'" in sql
    assert "ui:widget.txt" not in sql
    # the two self-correcting payoff views are always defined
    assert "gap_orphaned_ui_true" in sql
    assert "gap_orphaned_ui_falsepos" in sql
    assert "ui_observed_active" in sql


def test_no_observed_data_is_safe(tmp_path):
    # Missing input: the tool still emits the view scaffold (0 edges) and exits 0,
    # so "no observed data yet" never reads as a crash or an empty file.
    out = tmp_path / "app_graph_observed.sql"
    r = _run(tmp_path / "does_not_exist.ndjson", out)
    assert r.returncode == 0, r.stderr
    sql = out.read_text(encoding="utf-8")
    assert "gap_orphaned_ui_true" in sql
    assert "0 observed UI->endpoint edges" in sql
