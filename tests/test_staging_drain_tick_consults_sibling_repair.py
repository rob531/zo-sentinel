"""The staging_drain tick must CONSULT tools/repair_staged_sibling_symbols.py.

That tool shipped 2026-10-05 (#6165) with a 13-pole self-test and no caller at all:
`dark_tools.py --assert-wired tools/repair_staged_sibling_symbols.py` was rc=1 on
origin/main d8168c3d4. A cure that nothing calls is the failure class this repo has
paid for repeatedly (doctrine R2: a merge is not an arming).

NEGATIVE CONTROL (doctrine R4): both tests below were run against chain_tick.py as it
stood at d8168c3d4 and both FAILED -- the first on the missing attribute, the second on
the missing call in main(). They have been observed RED on purpose; that observation is
what makes them evidence instead of an untested branch.
"""
import importlib.util
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TICK = os.path.join(ROOT, "tools", "staging_drain", "chain_tick.py")


def _load():
    spec = importlib.util.spec_from_file_location("c188_chain_tick", TICK)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


class _Proc:
    returncode = 0
    stderr = ""

    def __init__(self, stdout):
        self.stdout = stdout


def test_tick_invokes_the_sibling_symbol_repair(monkeypatch):
    mod = _load()
    assert hasattr(mod, "run_sibling_repair"), "the tick has no sibling-symbol step"
    assert os.path.isfile(mod.SIBLING_TOOL), mod.SIBLING_TOOL

    seen = []
    payload = '{"sites": 0, "services": 0, "proven_rename": [], "proven_module": []}'
    monkeypatch.setattr(mod.subprocess, "run",
                        lambda cmd, **kw: seen.append(cmd) or _Proc(payload))
    log = []
    assert mod.run_sibling_repair(log) == []
    assert seen, "run_sibling_repair launched nothing"
    assert seen[0][1] == mod.SIBLING_TOOL
    assert "--apply" in seen[0] and "--json" in seen[0]
    assert log and log[0]["step"].startswith("S5b")


def test_repaired_services_are_handed_back_for_re_census(monkeypatch):
    """A repair the re-census never hears about leaves the ledger reporting the old
    failure -- the drain would re-emit a builder directive for an already-fixed site."""
    mod = _load()
    payload = (
        '{"sites": 2, "services": 2,'
        ' "proven_rename": [{"file": "services/staged/alpha_svc/router.py",'
        ' "line": 3, "want": "x", "new": "y"}],'
        ' "proven_module": [{"file": "services/staged/beta_svc/router.py",'
        ' "line": 4, "want": "z", "owner": "contract"}]}'
    )
    monkeypatch.setattr(mod.subprocess, "run", lambda cmd, **kw: _Proc(payload))
    log = []
    assert mod.run_sibling_repair(log) == ["alpha_svc", "beta_svc"]
    assert log[0]["repaired_services"] == ["alpha_svc", "beta_svc"]


def test_main_unions_the_sibling_repair_into_the_recensus():
    with open(TICK, encoding="utf-8") as fh:
        body = fh.read().split("def main(", 1)[1]
    assert "run_sibling_repair(log)" in body, (
        "main() does not call run_sibling_repair -- the step exists but never fires")


def test_a_tool_crash_is_not_a_tick_failure(monkeypatch):
    """Refusal is the common case; an unparseable stdout must not raise."""
    mod = _load()

    class Bad(_Proc):
        returncode = 1

    monkeypatch.setattr(mod.subprocess, "run", lambda cmd, **kw: Bad("traceback, not json"))
    assert mod.run_sibling_repair([]) == []
