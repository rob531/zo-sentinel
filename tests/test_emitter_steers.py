"""The emitter is taught what the gates refuse (BUILDER_ANTIPATTERNS AP-009, AP-010).

A gate alone is half a spineful control: it refuses the output but the emitter
keeps producing it. Each class with an armed detect_post also carries a
detect_pre steer in the engine prompt (_steer_context). Poles:
  * a directive that can reach the class gets its steer line;
  * a directive that cannot is byte-identical to before (no steer block);
  * every AP entry's detect_post names files that exist (the taught record
    cannot point at a gate that was renamed away).
"""
from __future__ import annotations

import os
import re

import goose_runner as gr

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _hermetic(monkeypatch):
    monkeypatch.setattr(gr, "_schema_ground_context", lambda d: ("", "no_table_matched"))
    monkeypatch.setattr(gr, "_bus_table_context", lambda: "")
    monkeypatch.setattr(gr, "_reads_context", lambda d: "")


def test_http_directive_gets_retry_steer(monkeypatch):
    _hermetic(monkeypatch)
    out = gr._engine_task("T", {"task": "ingest the OSV feed from https://api.osv.dev"})
    assert "AP-009" in out and "request_with_retry" in out
    assert "AP-010" not in out


def test_scoring_directive_gets_echo_steer(monkeypatch):
    _hermetic(monkeypatch)
    out = gr._engine_task("T", {"task": "add a compute_vendor_signal to the scorer"})
    assert "AP-010" in out and "trust_score" in out
    assert "AP-009" not in out


def test_unrelated_directive_is_unchanged(monkeypatch):
    _hermetic(monkeypatch)
    assert gr._engine_task("T", {"task": "build_x_dashboard_page"}) == "T"


def test_steer_also_on_matched_path(monkeypatch):
    _hermetic(monkeypatch)
    monkeypatch.setattr(gr, "_schema_ground_context",
                        lambda d: ("REAL SCHEMA\n  Foo (table foo): a", "matched"))
    out = gr._engine_task("T", {"task": "register the webhook receiver"})
    assert "REAL SCHEMA" in out and "AP-009" in out


def test_every_steer_has_an_antipattern_entry():
    text = open(os.path.join(ROOT, "BUILDER_ANTIPATTERNS.md"), encoding="utf-8").read()
    for ap, _rx, _line in gr._STEERS:
        assert re.search(rf"^## {ap}\s", text, re.M), f"{ap} steered but not recorded"


def test_detect_post_files_exist():
    text = open(os.path.join(ROOT, "BUILDER_ANTIPATTERNS.md"), encoding="utf-8").read()
    missing = []
    for block in re.split(r"^## ", text, flags=re.M):
        if not re.match(r"AP-00(8|9)|AP-01\d", block):
            continue
        m = re.search(r"detect_post\s*:(.*?)(?:\n\s*\n|\Z)", block, re.S)
        assert m, block[:20] + " has no detect_post"
        for path in re.findall(r"\b((?:tests|tools)/[\w/]+\.py)\b", m.group(1)):
            if not os.path.exists(os.path.join(ROOT, path)):
                missing.append((block[:6], path))
    assert not missing, missing


def test_db_fetch_is_not_outbound_http(monkeypatch):
    """Review of #16289: `fetch` alone over-matched DB reads."""
    _hermetic(monkeypatch)
    for task in ("fetch rows from mcp_server_registry and render a table",
                 "poll the directive queue table every 10 min",
                 "ingest the scored rows into the report"):
        assert "AP-009" not in gr._engine_task("T", {"task": task}), task
    for task in ("fetch the OSV feed for each package", "poll the GitHub API for releases",
                 "ingest advisories from the NVD api", "call https://example.test/x"):
        assert "AP-009" in gr._engine_task("T", {"task": task}), task


def test_fly_bound_output_is_not_told_to_import_http_retry(monkeypatch):
    """Review of #16289: http_retry.py is not in the Fly image."""
    _hermetic(monkeypatch)
    fly = gr._engine_task("T", {"task": "fetch the OSV feed",
                                "output_path": "services/staged/osv_x/router.py"})
    assert "AP-009" in fly and "do not import it" in fly
    assert "request_with_retry" not in fly
    factory = gr._engine_task("T", {"task": "fetch the OSV feed", "output_path": "osv_ingestor_v2.py"})
    assert "request_with_retry" in factory


def test_http_retry_is_not_shipped_to_fly():
    """The premise of the Fly steer: if this ever changes, the steer can be unified."""
    df = open(os.path.join(ROOT, "Dockerfile"), encoding="utf-8").read()
    assert "http_retry" not in df
