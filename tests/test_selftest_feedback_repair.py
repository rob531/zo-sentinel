"""test_selftest_feedback_repair.py -- deterministic self-test feedback-repair.

The 2026-10-29 logic-leaf REDs persisted through BOTH prompt-level fixes (exemplar
inlining #11750, ORM-mandate schema header #11751): the cheap rung still improvised a
raw-SQL seed INSERT with a placeholder/column mismatch against the 15-col scoring table,
and a BLIND ghost retry just re-rolled the same dice. The repair re-runs the engine ONCE
with the model's own quoted self-test failure plus a deterministic raw-SQL lint, so the
retry is informed rather than random.

Three units, each two-poled:
  * _raw_sql_lint    -- fires on raw SQL, silent on ORM idiom.
  * _selftest_feedback -- always quotes the error; adds the ORM directive ONLY when the
    lint fires (a clean build gets no needless reminder).
  * _selftest_gate stash -- sets _LAST_SELFTEST_RED on a RED run, clears it on PASS, so a
    stale error can never leak into a later repair.
Hermetic: the stash poles run a self-contained script via an absolute output_file; no
app.* imports, no network, no PROJECT_DIR dependency.
"""
from __future__ import annotations

import goose_runner as gr

_RAW_INSERT = (
    "cur.execute('INSERT INTO mcp_llm_axis_scores (label, p_top) "
    "VALUES (?, ?)', (lbl, pt))"
)
_RAW_TEXT = 'db.execute(text("SELECT count(*) FROM mcp_llm_axis_scores"))'
_ORM_CODE = (
    "from app.models import McpLlmAxisScore\n"
    "from sqlalchemy import select\n"
    "rows = db.execute(select(McpLlmAxisScore.label, McpLlmAxisScore.p_top)).all()\n"
    "db.add(McpLlmAxisScore(label='x', p_top=0.1))\n"
)


# --- _raw_sql_lint: two poles --------------------------------------------------

def test_raw_sql_lint_fires_on_raw():
    assert gr._raw_sql_lint(_RAW_INSERT)                       # non-empty
    assert "INSERT" in gr._raw_sql_lint(_RAW_INSERT)
    assert gr._raw_sql_lint(_RAW_TEXT)
    assert "text()" in gr._raw_sql_lint(_RAW_TEXT)


def test_raw_sql_lint_clean_on_orm():
    # ORM passes a select()/insert() CONSTRUCT (no quote after execute() and no
    # VALUES(...) list) and seeds via .add(Model(...)) -> no signal.
    assert gr._raw_sql_lint(_ORM_CODE) == ""
    assert gr._raw_sql_lint("") == ""


# --- _selftest_feedback: always quotes; ORM directive is conditional -----------

def test_feedback_quotes_the_error():
    fb = gr._selftest_feedback("AssertionError: body missing 'risk_tier'", _ORM_CODE)
    assert "FAILED ITS ACCEPTANCE SELF-TEST" in fb
    assert "AssertionError: body missing 'risk_tier'" in fb
    assert "re-emit the COMPLETE file" in fb


def test_feedback_adds_orm_directive_only_when_raw_sql():
    with_raw = gr._selftest_feedback("boom", _RAW_INSERT)
    without_raw = gr._selftest_feedback("boom", _ORM_CODE)
    assert "Use the SQLAlchemy ORM only" in with_raw      # pole: raw -> hard ORM push
    assert "Use the SQLAlchemy ORM only" not in without_raw  # pole: clean -> no noise


# --- _selftest_gate stash: set on RED, clear on PASS ---------------------------

def _run_gate(tmp_path, monkeypatch, body, did):
    # Hermetic: point PROJECT_DIR at tmp (so the self-test subprocess cwd exists and the
    # module resolves under `python -m`) and silence log() -- the CI sandbox has no
    # /home/workspace, so an unpatched log/cwd would FileNotFound before the verdict.
    monkeypatch.setattr(gr, "PROJECT_DIR", tmp_path)
    monkeypatch.setattr(gr, "log", lambda *a, **k: None)
    p = tmp_path / f"{did}.py"
    p.write_text(body, encoding="utf-8")
    directive = {"output_file": str(p)}   # absolute -> declared_output returns it as-is
    return gr._selftest_gate(directive, did)


def test_selftest_gate_stashes_red(tmp_path, monkeypatch):
    did = "red_case"
    gr._LAST_SELFTEST_RED.pop(did, None)
    body = "if __name__ == '__main__':\n    print('about to fail')\n    raise SystemExit(1)\n"
    ok = _run_gate(tmp_path, monkeypatch, body, did)
    assert ok is False                                   # RED blocks completion
    assert did in gr._LAST_SELFTEST_RED                  # ... and the error is stashed
    assert "about to fail" in gr._LAST_SELFTEST_RED[did]


def test_selftest_gate_clears_stash_on_pass(tmp_path, monkeypatch):
    did = "pass_case"
    gr._LAST_SELFTEST_RED[did] = "stale error from a prior attempt"
    body = "if __name__ == '__main__':\n    print('PASS')\n"
    ok = _run_gate(tmp_path, monkeypatch, body, did)
    assert ok is True                                    # PASS
    assert did not in gr._LAST_SELFTEST_RED              # stale error must not leak
