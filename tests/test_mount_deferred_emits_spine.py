"""Both poles of the spine-emit step in tools/mount_deferred_router.py.

cycle-0156, 2026-09-29. --apply used to END with a printed instruction:

    NEXT (not run for you -- the generator is the spine's own oracle):
      python tools/generate_spine.py --emit .

That instruction was followed one step late in this very cycle and the next
ratchet run failed with *"12 new unmounted router(s) neither mounted nor
declared"*: the modules had left reachability_deferred.json while nothing under
app/ imported them. A printed instruction is not a step. The tool now calls the
generator itself -- and REPORTS FAILURE, because a mount that is registered but
not in the spine is a half-applied state that reds pr-gates.
"""
import importlib.util
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOOL = os.path.join(ROOT, "tools", "mount_deferred_router.py")


class _P(object):
    def __init__(self, rc, out="", err=""):
        self.returncode, self.stdout, self.stderr = rc, out, err


def _load():
    spec = importlib.util.spec_from_file_location("mount_deferred_router", TOOL)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["mount_deferred_router"] = mod
    spec.loader.exec_module(mod)
    return mod


def test_emit_ok_pole():
    m = _load()
    seen = {}

    def runner(argv):
        seen["argv"] = argv
        return _P(0, "wrote app\\_spine_generated.py (59 services)\n")

    ok, out = m._emit_spine(runner=runner)
    assert ok is True
    assert "59 services" in out
    assert seen["argv"][1].endswith(os.path.join("tools", "generate_spine.py"))
    assert seen["argv"][2:] == ["--emit", "."]


def test_emit_failure_pole_is_reported_not_swallowed():
    """THE NEGATIVE CONTROL. A refusing generator must come back False."""
    m = _load()
    ok, out = m._emit_spine(runner=lambda a: _P(2, "", "toml parse error"))
    assert ok is False
    assert "toml parse error" in out


def test_the_generator_is_the_only_oracle():
    """The tool must CALL generate_spine.py, never re-derive the spine."""
    src = open(TOOL, encoding="utf-8").read()
    assert "generate_spine.py" in src
    assert "_emit_spine(" in src


def test_the_step_is_never_a_printed_instruction_again():
    """The defect was a print, not a mention.

    A naive `"NEXT (not run for you" not in src` fails on the CURE, because
    _emit_spine's own docstring QUOTES the retired instruction as the exhibit.
    That is the rule_echo trap the ratchet names in its own comments: *to a
    substring match, a record of a retired rule is indistinguishable from
    still obeying it*. So match the EXECUTABLE form -- a print statement --
    not the words.
    """
    src = open(TOOL, encoding="utf-8").read()
    printed = [ln.strip() for ln in src.splitlines()
               if ln.strip().startswith("print(") and "NEXT (" in ln]
    assert printed == [], printed
    assert "NEXT (not run for you" in src, (
        "the exhibit is kept deliberately -- deleting the history is how the "
        "next lane re-invents the defect")


def test_apply_returns_nonzero_when_the_emit_fails():
    src = open(TOOL, encoding="utf-8").read()
    assert "return 1 if (refused or emit_failed) else 0" in src
    assert "SPINE EMIT FAILED" in src
