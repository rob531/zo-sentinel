"""GR-28 (SCORER_DISCRIMINATION_DESIGN_2026-10-09 2.1): no signal may read trust_score.

`process_server` writes `trust_score` back from the composite. `reputation` read it, so
pass N+1 scored pass N's answer (weight 0.20, zero information) and the input-hash gate,
which excludes trust_score, could not see the loop. The cure is at the origin: the
reputation signal is gone, the batch SELECT no longer fetches the column, and no
`compute_*` function may name it. Each rule has a negative control: the old reputation
source is fed to the same checker and must go RED.
"""
import ast
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC_PATH = os.path.join(ROOT, "signal_analyser.py")
WRITTEN_COLUMNS = ("trust_score", "verdict", "last_assessed")

OLD_REPUTATION = '''
def compute_reputation_score(server):
    trust_score = (server.get('trust_score') or 0)
    return {'signal_name': 'reputation', 'score': trust_score}
'''


def echo_readers(src: str) -> list[str]:
    """compute_* functions that mention a column process_server writes."""
    bad = []
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.FunctionDef) and node.name.startswith("compute_"):
            for sub in ast.walk(node):
                text = (sub.value if isinstance(sub, ast.Constant) and isinstance(sub.value, str)
                        else getattr(sub, "id", None) or getattr(sub, "attr", None))
                if text in WRITTEN_COLUMNS:
                    bad.append(f"{node.name} reads {text}")
                    break
    return bad


def weights(src: str) -> dict:
    for node in ast.parse(src).body:
        if isinstance(node, ast.Assign) and any(getattr(t, "id", "") == "SIGNAL_WEIGHTS" for t in node.targets):
            return ast.literal_eval(node.value)
    raise AssertionError("SIGNAL_WEIGHTS not found")


def src() -> str:
    with open(SRC_PATH, encoding="utf-8") as fh:
        return fh.read()


def test_negative_control_old_reputation_is_caught():
    assert echo_readers(OLD_REPUTATION) == ["compute_reputation_score reads trust_score"]


def test_no_signal_reads_a_column_the_pipeline_writes():
    assert echo_readers(src()) == []


def test_reputation_has_no_weight():
    w = weights(src())
    assert "reputation" not in w
    assert all(v > 0 for v in w.values()) and w


def test_batch_select_does_not_fetch_trust_score():
    s = src()
    selects = re.findall(r"SELECT\s+r\.server_id.*?FROM\s+mcp_server_registry", s, re.S)
    assert selects, "the batch SELECT moved; re-point this test"
    for sel in selects:
        assert "trust_score" not in sel, sel


def test_composite_is_invariant_to_prior_trust_score(monkeypatch):
    """Drive the real process_server with the bus stubbed: the composite it writes
    must not depend on the trust_score the registry row carried in."""
    sys.path.insert(0, ROOT)
    import signal_analyser as sa  # imports requests only; no network at import
    written = []
    monkeypatch.setattr(sa, "last_score_state", lambda sid: {})
    monkeypatch.setattr(sa, "ws_write", lambda table, rows: written.extend(rows))
    monkeypatch.setattr(sa, "ws_execute", lambda sql: None)
    monkeypatch.setattr(sa, "log", lambda msg: None)
    base = {"server_id": "x", "name": "demo-server", "url": "https://github.com/o/demo",
            "description": "d" * 120, "registry_source": "github", "scan_count": 3}

    def composite(prior):
        written.clear()
        assert sa.process_server(dict(base, trust_score=prior)) == "scored"
        names = {r["signal_name"] for r in written}
        assert "reputation" not in names
        return next(r["score"] for r in written if r["signal_name"] == "composite")

    assert composite(5.0) == composite(99.0)
