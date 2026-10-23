"""A truncated list must SAY it was truncated.

Measured 2026-09-10 on origin/main @ f147ff1f: `referent_verify.py --skip-routes`
printed `389 qualified column refs checked, 146 MISSING` and then exactly 25
`MISSING COLUMN` lines, with nothing between them saying the list had been cut
at a display cap. A lane censused stdout, counted 25 across 5 tables, and was
about to publish that as the whole backlog -- the summary number and the list
disagreed by 121 and the artefact did not know it.

This is R6 (unknown is not zero) at the display layer: a silently-capped list is
byte-indistinguishable from a complete one, so the reader gets the CAP as the
population.

Both poles are asserted here, because a test that only checks the notice
APPEARS would also pass if the notice were printed unconditionally -- which
would be a different lie in the same place.
"""
import importlib.util
import pathlib
import sys

_RV = pathlib.Path(__file__).resolve().parents[1] / "tools" / "referent_verify.py"


def _load():
    spec = importlib.util.spec_from_file_location("_rv_trunc", _RV)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["_rv_trunc"] = mod
    spec.loader.exec_module(mod)
    return mod


def test_cap_bites_and_says_so(capsys):
    """POSITIVE CONTROL: when the cap bites, the withheld count is named."""
    rv = _load()
    total = rv._print_capped(list(range(30)), 5, "  ", lambda i: f"item {i}",
                             "thing(s)")
    out = capsys.readouterr().out
    assert total == 30
    assert out.count("item ") == 5, "should print exactly `cap` items"
    assert "25 more" in out, "must name how many were withheld"
    assert "NOT SHOWN" in out
    assert "30 total" in out, "must name the true population"


def test_cap_does_not_bite_and_stays_quiet(capsys):
    """NEGATIVE CONTROL: an untruncated list must NOT claim truncation.

    Without this pole, printing the notice unconditionally would pass the test
    above while telling the reader something false on every complete list.
    """
    rv = _load()
    total = rv._print_capped([1, 2, 3], 5, "  ", lambda i: f"item {i}",
                             "thing(s)")
    out = capsys.readouterr().out
    assert total == 3
    assert out.count("item ") == 3
    assert "NOT SHOWN" not in out
    assert "more" not in out


def test_exactly_at_the_cap_is_not_truncated(capsys):
    """The off-by-one that would re-introduce the lie in the other direction."""
    rv = _load()
    rv._print_capped([1, 2, 3, 4, 5], 5, "  ", lambda i: f"item {i}", "thing(s)")
    out = capsys.readouterr().out
    assert out.count("item ") == 5
    assert "NOT SHOWN" not in out


def test_no_silent_slice_survives_in_a_print_path():
    """Census, not a spot-fix: every display cap in this file announces itself.
    One door of six is not a cure.

    The first draft of this test looked for `[:N]` and `print` on the SAME
    line, and it passed against the unpatched file -- the four real offenders
    are `for t, sites in list(missing_t.items())[:25]:`, where the slice is in
    the loop header and the print is in the body. An assertion that cannot go
    red against the defect it names is a rubber stamp, so it is keyed on the
    loop header here and was re-observed RED against the pre-patch file.
    """
    import re

    lines = _RV.read_text(encoding="utf-8").splitlines()
    offenders = []
    for i, line in enumerate(lines):
        if not re.match(r"\s*for\b.*\[:\d+\]\s*:\s*$", line):
            continue
        window = "\n".join(lines[i:i + 14])
        if "print" not in window:
            continue  # not a display path
        if "_print_capped" in window or "NOT SHOWN" in window:
            continue  # the cap announces itself
        offenders.append(f"{i + 1}: {line.strip()}")
    assert not offenders, (
        "these loops print a silently-truncated list -- route them through "
        "_print_capped or emit an explicit withheld-count line: "
        + "; ".join(offenders))


def test_json_report_carries_the_true_site_count():
    """The JSON caps ref sites at 5 per referent. A consumer counting sites
    from it would under-report for the same reason; the true count rides
    alongside."""
    src = _RV.read_text(encoding="utf-8")
    assert "missing_site_counts" in src
    assert src.count("missing_site_counts") >= 2, "tables AND columns"
