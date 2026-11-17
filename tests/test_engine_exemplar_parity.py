"""test_engine_exemplar_parity.py -- the engine fallback must inline the exemplar.

Root cause of the 2026-10-29 service-chain logic-build stall: the engine fallback
had schema-COLUMN parity with the goose canary (via _schema_ground_context) but not
exemplar-PATTERN parity -- it knew the real columns but not the ORM idiom, so it
improvised a raw SQL INSERT (placeholder-count bug) and the self-test went RED.
_reads_context inlines the directive's declared `reads` (the exemplar) into the
engine prompt, since a single-shot completion has no filesystem to read them.

Two poles:
  * WITH reads: the exemplar content appears in the engine task text.
  * WITHOUT reads: behaviour is byte-identical to before (no reference block).
Hermetic: PROJECT_DIR + the schema/bus context builders are monkeypatched.
"""
from __future__ import annotations

import goose_runner as gr


def _exemplar(tmp_path, body="from app.models import X  # ORM idiom"):
    d = tmp_path / "services" / "_exemplar"
    d.mkdir(parents=True, exist_ok=True)
    (d / "logic.py").write_text(body, encoding="utf-8")


# --- _reads_context (pure) ----------------------------------------------------

def test_reads_context_inlines_file(tmp_path, monkeypatch):
    monkeypatch.setattr(gr, "PROJECT_DIR", tmp_path)
    _exemplar(tmp_path, "SENTINEL_ORM_PATTERN")
    out = gr._reads_context({"reads": ["services/_exemplar/logic.py"]})
    assert "SENTINEL_ORM_PATTERN" in out
    assert "REFERENCE FILE" in out
    assert "do NOT write raw SQL" in out


def test_reads_context_empty_cases(tmp_path, monkeypatch):
    monkeypatch.setattr(gr, "PROJECT_DIR", tmp_path)
    assert gr._reads_context({}) == ""
    assert gr._reads_context({"reads": []}) == ""
    assert gr._reads_context({"reads": "notalist"}) == ""
    assert gr._reads_context({"reads": ["services/_exemplar/missing.py"]}) == ""


# --- _engine_task: the fix (WITH reads) and the control (WITHOUT) -------------

def test_engine_task_inlines_exemplar_when_reads_present(tmp_path, monkeypatch):
    monkeypatch.setattr(gr, "PROJECT_DIR", tmp_path)
    _exemplar(tmp_path, "SENTINEL_ORM_PATTERN")
    monkeypatch.setattr(gr, "_schema_ground_context", lambda d: ("", "no_table_matched"))
    monkeypatch.setattr(gr, "_bus_table_context", lambda: "")
    out = gr._engine_task("BUILD TASK", {"reads": ["services/_exemplar/logic.py"], "task": "build_x_logic"})
    assert "BUILD TASK" in out
    assert "SENTINEL_ORM_PATTERN" in out           # the fix: exemplar now in the engine prompt


def test_engine_task_unchanged_without_reads(tmp_path, monkeypatch):
    monkeypatch.setattr(gr, "PROJECT_DIR", tmp_path)
    monkeypatch.setattr(gr, "_schema_ground_context", lambda d: ("", "no_table_matched"))
    monkeypatch.setattr(gr, "_bus_table_context", lambda: "")
    out = gr._engine_task("BUILD TASK", {"task": "build_x"})
    assert out == "BUILD TASK"                      # no reference block, byte-identical to before


def test_engine_task_exemplar_also_on_matched_path(tmp_path, monkeypatch):
    monkeypatch.setattr(gr, "PROJECT_DIR", tmp_path)
    _exemplar(tmp_path, "SENTINEL_ORM_PATTERN")
    monkeypatch.setattr(gr, "_schema_ground_context", lambda d: ("REAL SCHEMA\n  Foo (table foo): a, b", "matched"))
    monkeypatch.setattr(gr, "_bus_table_context", lambda: "")
    out = gr._engine_task("BUILD TASK", {"reads": ["services/_exemplar/logic.py"], "task": "build_x_logic"})
    assert "REAL SCHEMA" in out and "SENTINEL_ORM_PATTERN" in out   # columns AND pattern
