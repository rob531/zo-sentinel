"""Pure-function tests for treewalk.score -- no browser, runs anywhere."""
import treewalk as TW


def _rep(**kw):
    base = {"loaded": True, "interactive_total": 4, "clicks_attempted": 4, "clicks_clean": 4,
            "console_errors": [], "js_exceptions": [], "failed_requests": [],
            "unnamed_interactives": 0, "dom_mutations_seen": 6}
    base.update(kw); return base


def test_clean_wired_app_scores_high():
    s, _ = TW.score(_rep()); assert s == 100


def test_no_load_is_zero():
    s, _ = TW.score(_rep(loaded=False)); assert s == 0


def test_js_exception_penalized():
    s, _ = TW.score(_rep(js_exceptions=["boom"], clicks_clean=3))
    assert s < 100 and s >= 0


def test_dead_ui_no_mutations_penalized():
    s, br = TW.score(_rep(dom_mutations_seen=0))
    assert br.get("clicks_dont_change_dom") == -20


def test_unnamed_controls_penalized():
    s, br = TW.score(_rep(unnamed_interactives=3))
    assert br.get("unnamed_controls") == -6


def test_discriminates_good_from_broken():
    good, _ = TW.score(_rep())
    broken, _ = TW.score(_rep(clicks_clean=1, dom_mutations_seen=0,
                              console_errors=["e"], js_exceptions=["a", "b"],
                              unnamed_interactives=1))
    assert good - broken >= 40   # the scorer clearly separates quality
