"""Repo-CI guard for the bake-off scorer: deterministic, discriminates quality,
imports without a browser (playwright import is lazy)."""
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools", "bakeoff"))
import treewalk as TW


def _rep(**kw):
    base = {"loaded": True, "interactive_total": 4, "clicks_attempted": 4, "clicks_clean": 4,
            "console_errors": [], "js_exceptions": [], "failed_requests": [],
            "unnamed_interactives": 0, "dom_mutations_seen": 6}
    base.update(kw); return base


def test_clean_high_and_noload_zero():
    assert TW.score(_rep())[0] == 100
    assert TW.score(_rep(loaded=False))[0] == 0


def test_scorer_discriminates_good_from_broken():
    good = TW.score(_rep())[0]
    broken = TW.score(_rep(clicks_clean=1, dom_mutations_seen=0, console_errors=["e"],
                           js_exceptions=["a", "b"], unnamed_interactives=1))[0]
    assert good - broken >= 40
