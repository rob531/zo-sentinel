"""fu_ledger write-side `fu` ARGUMENT shape: a number must resolve, not crash.

FU-397 / improvement-loop cycle-0069.

Cycle-0065 cured the FIRST argument of `append_log`/`insert_key` (a path or a
bad type where `lines` belongs now raises a TypeError naming the signature) and
cycle-0066 cured the read side. The `sanctioned-writer-api-shape` family kept
biting anyway -- 18 stalls across 10 lanes in the seven days to 2026-09-04 --
because the two most recent bites were never about `lines` at all:

  2026-09-03T19:56Z  prod-drift-sentinel  append_log(lines, fu="FU-235", ...)
  2026-09-04T00:46Z  improvement-loop     append_log(lines, 395, ...)

Both passed a correct list and a present text, sailed through the cycle-0065
guard, and died four frames down with `AttributeError: 'str' object has no
attribute 'keys'` -- an error indistinguishable from a genuine module bug and
which never names the argument at fault. The mistake is not unreasonable: `fu`
is spelled like a number everywhere the fleet reads about it, `FU.num` is a
STRING with leading zeros, and the `_MISSING` sentinel makes
`inspect.signature` print `fu=<object object at 0x...>`, so the one recovery a
bitten caller reaches for tells them nothing.

The cure is RECOVERY, not a louder refusal (HARNESS_DOCTRINE R7): resolve the
number against the lines the caller already handed us.

Every test carries BOTH poles. The negative pole is the shape that used to
crash; the POSITIVE pole is the FU-object call every correct caller already
makes, which must be unchanged -- a cure that breaks the shape people get right
is not a cure. `test_unknown_number_raises` is the anti-guess control: the one
outcome that must never happen is a silent no-op on an FU that is not there,
because that is the defect this whole family is made of.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

MODULE = Path(__file__).resolve().parents[1] / "tools" / "fu" / "fu_ledger.py"

LEDGER = (
    "# Follow-ups\r\n"
    "\r\n"
    "### FU-035 | leading-zero entry\r\n"
    "- date: 2026-01-01 - status: OPEN\r\n"
    "- log:\r\n"
    "  - 2026-01-01 opened\r\n"
    "\r\n"
    "### FU-395 | the entry under test\r\n"
    "- date: 2026-09-04 - status: OPEN\r\n"
    "- log:\r\n"
    "  - 2026-09-04 pre-existing bullet\r\n"
    "\r\n"
).splitlines(keepends=True)

MARK = "fu-arg-shape probe bullet"


@pytest.fixture(scope="module")
def fu_ledger():
    spec = importlib.util.spec_from_file_location("fu_ledger_under_test", MODULE)
    assert spec and spec.loader, "fu_ledger.py not importable at %s" % MODULE
    mod = importlib.util.module_from_spec(spec)
    # sys.modules BEFORE exec_module: the dataclass body resolves its own
    # module by name and dies without this, identically on both copies.
    sys.modules["fu_ledger_under_test"] = mod
    spec.loader.exec_module(mod)
    return mod


def lines():
    return list(LEDGER)


def bullets(ls):
    return sum(1 for l in ls if MARK in l)


def test_fu_object_still_works_unchanged(fu_ledger):
    """POSITIVE CONTROL. The documented shape must be untouched."""
    ls = lines()
    fu = {str(f.num).lstrip("0"): f for f in fu_ledger.parse(ls)}["395"]
    fu_ledger.append_log(ls, fu, "2026-09-04 " + MARK)
    assert bullets(ls) == 1
    assert "".join(ls).count("\r\n") == len(ls), "terminator class moved"


@pytest.mark.parametrize("designator", [395, "395", "FU-395", "fu-395", " 395 "])
def test_append_log_accepts_a_number(fu_ledger, designator):
    """NEGATIVE POLE: every one of these raised AttributeError before FU-397."""
    ls = lines()
    fu_ledger.append_log(ls, designator, "2026-09-04 " + MARK)
    assert bullets(ls) == 1, "designator %r did not write the bullet" % (designator,)


def test_leading_zero_number_resolves(fu_ledger):
    """FU.num is '035'; `f.num == 35` is always False, which reads as absent."""
    ls = lines()
    fu_ledger.append_log(ls, 35, "2026-09-04 " + MARK)
    assert bullets(ls) == 1
    # and it landed in FU-035, not in the other entry
    idx = [i for i, l in enumerate(ls) if MARK in l][0]
    head = [i for i, l in enumerate(ls) if l.startswith("### FU-")]
    owner = max(h for h in head if h < idx)
    assert "FU-035" in ls[owner]


def test_insert_key_accepts_a_number(fu_ledger):
    """Same door on the sibling writer -- censused in the SAME commit."""
    ls = lines()
    fu_ledger.insert_key(ls, "395", "class", "defect")
    assert any(l.startswith("- class: defect") for l in ls)


def test_unknown_number_raises_and_names_it(fu_ledger):
    """ANTI-GUESS CONTROL. A silent no-op here is the defect, not the cure."""
    ls = lines()
    with pytest.raises(ValueError) as exc:
        fu_ledger.append_log(ls, 999, "2026-09-04 " + MARK)
    assert "999" in str(exc.value), "the error must name the FU that was missing"
    assert bullets(ls) == 0, "nothing may be written for an unknown FU"


def test_non_fu_shaped_argument_still_raises_typeerror(fu_ledger):
    """A dict or an object is not a number and must not be guessed at."""
    ls = lines()
    with pytest.raises(TypeError):
        fu_ledger.append_log(ls, {"num": "395"}, "2026-09-04 " + MARK)
    assert bullets(ls) == 0
