"""The build badge: it must be present, correct, degraded when it should be,
and it must be OBSERVABLE FAILING.

Every assertion here was run RED before it was run green (FU-249): the badge
module was stubbed to return the page unchanged and each positive test failed,
which is the only evidence that these tests test anything at all.
"""
from __future__ import annotations

import importlib

import pytest

from app import build_badge


def _reload(monkeypatch, **env):
    for k in ("GIT_SHA", "BUILD_TIME", "ENV", "APP_ENV"):
        monkeypatch.delenv(k, raising=False)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    return importlib.reload(build_badge)


def test_badge_carries_the_running_sha(monkeypatch):
    m = _reload(monkeypatch, GIT_SHA="abc1234def5678", BUILD_TIME="2026-09-09T13:40:11Z")
    out = m.inject("<html><body>hi</body></html>")
    assert 'data-build-sha="abc1234def5678"' in out
    assert "abc1234d" in out            # short form is what the eye reads
    assert "09-09 13:40" in out


def test_missing_git_sha_renders_DEGRADED_not_plausible(monkeypatch):
    """A build that cannot name itself must LOOK wrong.

    prod served git_sha 'unknown' for its entire early life. A badge that
    quietly rendered a blank or a friendly 'dev' would reproduce exactly that
    invisibility on the surface introduced to end it.
    """
    m = _reload(monkeypatch, BUILD_TIME="2026-09-09T13:40:11Z")
    out = m.inject("<html><body>hi</body></html>")
    assert 'data-build-sha="unknown"' in out
    assert "#ecdcae" in out             # the amber degraded style, not the muted one
    assert "CANNOT IDENTIFY ITSELF" in out


def test_injection_is_idempotent(monkeypatch):
    """_render and a caller could both stamp; two badges is a UI bug."""
    m = _reload(monkeypatch, GIT_SHA="abc1234def5678")
    once = m.inject("<html><body>hi</body></html>")
    assert m.inject(once) == once
    assert once.count("id=\"zo-build-badge\"") == 1


def test_explicit_token_wins_over_body_placement(monkeypatch):
    m = _reload(monkeypatch, GIT_SHA="abc1234def5678")
    out = m.inject("<html><body><header>__BUILD_BADGE__</header></body></html>")
    assert "__BUILD_BADGE__" not in out
    assert out.index("zo-build-badge") < out.index("</header>")


def test_page_without_body_is_still_stamped(monkeypatch):
    m = _reload(monkeypatch, GIT_SHA="abc1234def5678")
    assert "zo-build-badge" in m.inject("<div>fragment</div>")


def test_injection_never_breaks_the_page(monkeypatch):
    """A stamping failure must cost a badge, never a render.

    An unstamped page is a triage inconvenience; a page that 500s is an outage,
    and this code runs on every HTML response.
    """
    m = _reload(monkeypatch, GIT_SHA="abc1234def5678")
    monkeypatch.setattr(m, "badge_html", lambda *a, **k: 1 / 0)
    page = "<html><body>hi</body></html>"
    assert m.inject(page) == page


def test_values_are_escaped(monkeypatch):
    """GIT_SHA is a build arg -- it is not, by construction, safe markup."""
    m = _reload(monkeypatch, GIT_SHA='"><script>alert(1)</script>')
    out = m.inject("<html><body>hi</body></html>")
    assert "<script>alert(1)</script>" not in out
    assert "&lt;script&gt;" in out


def test_unparseable_build_time_is_shown_not_dropped(monkeypatch):
    m = _reload(monkeypatch, GIT_SHA="abc1234def5678", BUILD_TIME="not-a-date")
    assert "not-a-date" in m.inject("<html><body>hi</body></html>")


# --- the asserter -----------------------------------------------------------

def test_asserter_reads_the_sha_a_page_claims():
    from tools import ui_version_assert as uva
    m = _reload_free(GIT_SHA="deadbeefcafe")
    page = m.inject("<html><body>x</body></html>")
    assert uva.badge_sha(page) == "deadbeefcafe"


def test_asserter_does_not_mistake_stray_text_for_a_badge():
    """A page mentioning data-build-sha in prose is NOT a stamped page.

    A matcher loose enough to pass here would report a badge no triager can see.
    """
    from tools import ui_version_assert as uva
    assert uva.badge_sha('<p>set data-build-sha="abc" somewhere</p>') is None
    assert uva.badge_sha("<html><body>no badge</body></html>") is None


def _reload_free(**env):
    import os
    for k, v in env.items():
        os.environ[k] = v
    return importlib.reload(build_badge)


@pytest.mark.parametrize("sha,expected", [
    ("abc1234def5678", "abc1234d"),
    ("unknown", "unknown"),
    ("", "unknown"),
])
def test_short_sha(sha, expected):
    assert build_badge.short_sha(sha) == expected
