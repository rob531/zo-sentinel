"""FU-189 -- `float()` on a CVSS VECTOR STRING, which can never succeed.

Surfaced the moment FU-187 stopped the daemon dying on its first cycle: the
priming cycle now logs and continues, and what it logged was
`could not convert string to float: 'CVSS'`.

OSV reports CVSS severity as a vector, not a scalar:
    {"type": "CVSS_V3", "score": "CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:H/A:H"}
The old expression `float(score.split(':')[0] if ':' in score else score)` takes
the first colon-delimited segment of exactly that shape -- the literal 'CVSS' --
so it raised on 100% of real CVSS_V3 entries. Every assertion here carries its
negative control against the pre-fix source.
"""

import contextlib
import io
import os
import types

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(REPO, "threat_intel_ingestor.py")

# The exact shape OSV returns, and the exact string that raised in prod.
REAL_OSV_VECTOR = "CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:H/A:H"

PREFIX_SEVERITY_BLOCK = """                severity_level = 'medium'
                if severity:
                    for s in severity:
                        if s.get('type') == 'CVSS_V3':
                            score = float(s.get('score', '0').split(':')[0] if ':' in s.get('score', '0') else s.get('score', '0'))
"""


@contextlib.contextmanager
def _no_makedirs():
    real = os.makedirs
    os.makedirs = lambda *a, **k: None
    try:
        yield
    finally:
        os.makedirs = real


def _load(src_text=None, name="tii_cvss"):
    if src_text is None:
        src_text = io.open(SRC, encoding="utf-8").read()
    mod = types.ModuleType(name)
    mod.__file__ = SRC
    with _no_makedirs():
        exec(compile(src_text, SRC, "exec"), mod.__dict__)
    return mod


@pytest.fixture(scope="module")
def src():
    return io.open(SRC, encoding="utf-8").read()


@pytest.fixture(scope="module")
def tii():
    return _load()


# ------------------------------------------------------------------ the parser

def test_cvss_vector_yields_none_not_an_exception(tii):
    """The prod string. A vector carries no scalar, so None is the honest answer."""
    assert tii.cvss_base_score(REAL_OSV_VECTOR) is None


@pytest.mark.parametrize("raw,expected", [
    ("9.8", 9.8),
    ("7.5", 7.5),
    (9.8, 9.8),
    (7, 7.0),
    (None, None),
    ("", None),
    ("   ", None),
    ("CVSS:3.0/AV:L/AC:H", None),
    ("not a number", None),
    ([], None),
    ({}, None),
])
def test_cvss_base_score_never_raises(tii, raw, expected):
    assert tii.cvss_base_score(raw) == expected


def test_negctl_prefix_float_on_the_vector_raises():
    """NEGATIVE CONTROL: the pre-fix expression, run verbatim on the prod string."""
    raw = REAL_OSV_VECTOR
    with pytest.raises(ValueError, match="could not convert string to float"):
        float(raw.split(":")[0] if ":" in raw else raw)
    # and it fails by producing exactly the token seen in the live log
    assert raw.split(":")[0] == "CVSS"


# ---------------------------------------------------- end-to-end through the loop

def _wire(mod, vulns):
    mod.get_mcp_servers_for_osv_scan = lambda: [
        {"server_id": "s1", "name": "n", "url": "https://npmjs.com/package/left-pad"}
    ]
    # OSV_ECOSYSTEMS has 8 entries and the scan loops over all of them; return
    # the vuln for ONE ecosystem so the count is 1 and not 8.
    mod.query_osv = lambda eco, pkg: (vulns if eco == "npm" else [])
    mod.log = lambda *a, **k: None
    mod.threat_already_recorded = lambda *a, **k: False
    mod.written = []

    def _ws_write(table, rows):
        # MUST return a truthy response: ws_write signals TOTAL failure by
        # returning None, and since FU-190 the caller checks that. list.append
        # returns None, so the original stub was accidentally mimicking a
        # rejected write -- exactly the bug FU-190 fixes.
        mod.written.append(rows)
        return {"ok": True}

    mod.ws_write = _ws_write
    return mod


CRITICAL_VULN = [{
    "id": "GHSA-xxxx",
    # must pass is_relevant_to_mcp, and carries a 'critical' keyword
    "summary": "Remote code execution in an mcp server package dependency",
    "severity": [{"type": "CVSS_V3", "score": REAL_OSV_VECTOR}],
}]


def test_a_vector_only_vuln_is_recorded_with_keyword_severity(tii):
    mod = _wire(_load(), CRITICAL_VULN)
    assert mod.process_osv_vulns() == 1, "the vuln was not recorded"
    assert len(mod.written) == 1
    # 'rce'/'remote code execution' is a 'critical' keyword, so the vector-only
    # severity degrades to a REAL signal rather than a flat default.
    assert mod.written[0]["severity"] == "critical"


def test_a_numeric_score_still_drives_the_band(tii):
    vuln = [dict(CRITICAL_VULN[0], summary="path traversal in an mcp server package",
                 severity=[{"type": "CVSS_V3", "score": "4.2"}])]
    mod = _wire(_load(), vuln)
    assert mod.process_osv_vulns() == 1
    assert mod.written[0]["severity"] == "medium"   # 4.2 -> medium band

    vuln_low = [dict(CRITICAL_VULN[0], summary="deprecated insecure mcp server package",
                     severity=[{"type": "CVSS_V3", "score": "2.0"}])]
    mod2 = _wire(_load(), vuln_low)
    assert mod2.process_osv_vulns() == 1
    assert mod2.written[0]["severity"] == "low"     # <4.0 band existed nowhere before


def test_negctl_prefix_process_osv_vulns_raises_on_the_vector(src):
    """NEGATIVE CONTROL, end to end: pre-fix, this vuln kills the scan."""
    assert PREFIX_SEVERITY_BLOCK.rstrip() not in src, "pre-fix block still in source"
    # Reconstruct the pre-fix severity handling in place of the fixed one.
    start = src.index("                # compute_threat_severity returns 'unknown'")
    end = src.index("                evidence = f'OSV:{vuln_id}", start)
    prefix_src = src[:start] + PREFIX_SEVERITY_BLOCK + """                            if score >= 9.0:
                                severity_level = 'critical'
                            break
""" + src[end:]
    assert prefix_src != src

    mod = _wire(_load(prefix_src, "tii_cvss_prefix"), CRITICAL_VULN)
    with pytest.raises(ValueError, match="could not convert string to float"):
        mod.process_osv_vulns()
