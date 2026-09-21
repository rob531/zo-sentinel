"""Build-identity badge: every rendered page names the build that served it.

WHY THIS EXISTS
---------------
`/version` has carried GIT_SHA / BUILD_TIME since 2026-07-14, and `accept_gate`
already REJECTS a deploy whose `/version.git_sha` is not the sha that was fired.
That makes the running build identifiable to a MACHINE. It has never been
identifiable to a PERSON looking at mcprisky.io: answering "which build am I
looking at?" meant leaving the page for an ops endpoint, and every screenshot in
every bug report was undated. Triage paid for that difference.

THE STAMP IS NOT A NUMBER ANYONE BUMPS. It is injected at RENDER time from the
same environment the image was built with (`GIT_SHA`, `BUILD_TIME` build args,
see Dockerfile), so it changes when -- and only when -- the image is rebuilt. A
version string a human has to remember to increment is a version string that
eventually lies; this one cannot drift from the artifact, because it IS the
artifact's identity read back.

A BUILD THAT CANNOT NAME ITSELF MUST LOOK WRONG. If GIT_SHA is missing the badge
renders DEGRADED (amber, the word "unknown") rather than something plausible.
That is the rule `accept_gate` already applies at the API, applied on the surface
a human actually looks at: prod served `"git_sha":"unknown"` for its entire early
life precisely because nothing that a person saw ever showed it.

ABSENCE OF A BADGE IS NOT EVIDENCE OF A GOOD BUILD (R6). This module only makes
the claim visible; `tools/ui_version_assert.py` is what turns it into an
assertion, and that is what runs after a deploy.
"""
from __future__ import annotations

import html as _html
import os
from datetime import datetime, timezone

UNKNOWN = "unknown"

# The attribute a machine reads. Deliberately on the badge element itself rather
# than on <html>: a scraper that finds the attribute has, by construction, found
# a rendered badge -- the two cannot come apart the way a meta tag and a visible
# element can.
BADGE_ATTR = "data-build-sha"
BADGE_ID = "zo-build-badge"
IDEMPOTENCY_MARK = 'id="%s"' % BADGE_ID

_BASE_STYLE = (
    "position:fixed;top:6px;right:8px;z-index:2147483000;"
    "font:600 10px/1.4 'JetBrains Mono',ui-monospace,Menlo,Consolas,monospace;"
    "letter-spacing:.04em;padding:2px 7px;border-radius:99px;"
    "text-decoration:none;opacity:.62;transition:opacity .15s;"
    "backdrop-filter:blur(3px);-webkit-backdrop-filter:blur(3px);"
)
_OK_STYLE = "background:rgba(17,13,29,.72);border:1px solid #2a2340;color:#a99cc7;"
_DEGRADED_STYLE = "background:rgba(60,40,0,.82);border:1px solid #ecdcae;color:#ecdcae;"


def build_identity() -> dict:
    """The build's own account of itself, read from the image environment.

    Returned verbatim -- no defaulting to a prettier value. `unknown` is a
    finding, not a blank to be filled in.
    """
    return {
        "git_sha": os.environ.get("GIT_SHA") or UNKNOWN,
        "built_at": os.environ.get("BUILD_TIME") or UNKNOWN,
        "env": os.environ.get("ENV") or os.environ.get("APP_ENV") or UNKNOWN,
    }


def short_sha(sha: str) -> str:
    if not sha or sha == UNKNOWN:
        return UNKNOWN
    return sha[:8]


def _short_built_at(built_at: str) -> str:
    """`2026-09-09T13:40:11Z` -> `09-09 13:40`. Unparseable input passes through.

    A timestamp this badge cannot parse is shown AS IT IS rather than dropped:
    the failure a person needs to see is the odd string, not a blank space where
    a date should be.
    """
    if not built_at or built_at == UNKNOWN:
        return UNKNOWN
    raw = built_at.strip().replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(raw)
    except ValueError:
        return built_at
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).strftime("%m-%d %H:%M")


def badge_html(identity: dict | None = None) -> str:
    """The badge element. Self-contained: no stylesheet, no script, no fetch.

    Inline style rather than a class, because these pages have no shared
    stylesheet and half of them are factory-emitted; a class would render
    unstyled on exactly the pages nobody is maintaining.
    """
    ident = identity or build_identity()
    sha = ident.get("git_sha") or UNKNOWN
    built_at = ident.get("built_at") or UNKNOWN
    degraded = sha == UNKNOWN

    label = "build %s" % short_sha(sha)
    stamp = _short_built_at(built_at)
    if stamp != UNKNOWN:
        label += " · " + stamp

    title = "git_sha %s\nbuilt_at %s\nenv %s\nclick for /version" % (
        sha, built_at, ident.get("env") or UNKNOWN)
    if degraded:
        title = ("THIS BUILD CANNOT IDENTIFY ITSELF -- GIT_SHA was not passed to "
                 "the image build.\n" + title)

    style = _BASE_STYLE + (_DEGRADED_STYLE if degraded else _OK_STYLE)
    return (
        '<a id="{id}" {attr}="{sha}" data-built-at="{built}" href="/version" '
        'title="{title}" style="{style}" '
        'onmouseover="this.style.opacity=1" onmouseout="this.style.opacity=.62"'
        '>{label}</a>'
    ).format(
        id=BADGE_ID,
        attr=BADGE_ATTR,
        sha=_html.escape(sha, quote=True),
        built=_html.escape(built_at, quote=True),
        title=_html.escape(title, quote=True),
        style=style,
        label=_html.escape(label),
    )


def inject(page_html: str, identity: dict | None = None) -> str:
    """Stamp `page_html`. Idempotent, and never raises on odd input.

    Placement: the explicit `__BUILD_BADGE__` token if a page wants to choose
    its own spot, else immediately before the LAST `</body>`. Failing both, the
    badge is appended -- a fragment with no body still gets stamped, because a
    page that renders is a page someone can screenshot.

    This runs on every HTML response. It must never be the reason a page 500s:
    any failure here returns the page unchanged, unstamped. An unstamped page is
    a triage inconvenience; a page that does not render is an outage.
    """
    try:
        if not page_html or IDEMPOTENCY_MARK in page_html:
            return page_html
        badge = badge_html(identity)
        if "__BUILD_BADGE__" in page_html:
            return page_html.replace("__BUILD_BADGE__", badge)
        lower = page_html.lower()
        at = lower.rfind("</body>")
        if at == -1:
            return page_html + badge
        return page_html[:at] + badge + page_html[at:]
    except Exception:
        return page_html
