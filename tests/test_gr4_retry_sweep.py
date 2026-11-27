"""GR-4: the RETRY_GAP_SWEEP.md "files not yet scanned" census, executed.

The 2026-04-21 sweep named 10 sibling files that likely made single-shot HTTP
calls. Five are in this repo (the other five -- zo_mesh/* -- live only on the
ZoComputer host and are registered separately). This test is the census: every
`requests.get/post` call in those five files must sit inside a retry loop or go
through http_retry, or be listed in INTENTIONAL_SINGLE_SHOT with its reason.
It also pins request_with_retry's contract (bare-call semantics, writes retried
only on ConnectionError).
"""
import ast
import os

import pytest
import requests

import http_retry
from http_retry import request_with_retry

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CENSUS = [
    "ecosystems_metadata_fetcher.py",
    "threat_intel_ingestor.py",
    "otx_ingestor.py",
    "mcp_registry_ingestor.py",
    "mcp_reference_servers_ingestor.py",
    "signal_bridge.py",  # named GAP in the sweep's scanned list
]
INTENTIONAL_SINGLE_SHOT = {
    # OSV enrichment is additive: a failure or 402/429 degrades this cycle by design
    # (see the comment at the call site) and the next cycle retries.
    ("threat_intel_ingestor.py", "query_osv"),
}


def _bare_calls(path):
    tree = ast.parse(open(path, encoding="utf-8").read())
    out = []

    def visit(node, in_loop, func):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            func = node.name
            in_loop = False
        if isinstance(node, (ast.For, ast.While)):
            in_loop = True
        if isinstance(node, ast.If) and isinstance(node.test, ast.Compare) \
                and isinstance(node.test.left, ast.Name) and node.test.left.id == "__name__":
            return
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) \
                and node.func.attr in ("get", "post") and isinstance(node.func.value, ast.Name) \
                and node.func.value.id in ("requests", "_req") and not in_loop:
            out.append((func, node.lineno))
        for ch in ast.iter_child_nodes(node):
            visit(ch, in_loop, func)

    visit(tree, False, None)
    return out


@pytest.mark.parametrize("fn", CENSUS)
def test_census_file_has_no_single_shot_http(fn):
    bare = [(f, ln) for f, ln in _bare_calls(os.path.join(ROOT, fn))
            if (fn, f) not in INTENTIONAL_SINGLE_SHOT]
    assert bare == [], f"{fn}: single-shot requests call(s) outside a retry loop: {bare}"


def test_census_detector_catches_the_pre_fix_shape(tmp_path):
    p = tmp_path / "x.py"
    p.write_text("import requests\ndef ws_query(s):\n    r = requests.post('u', json={})\n"
                 "    return r\ndef ok():\n    for _ in range(3):\n        requests.get('u')\n")
    assert _bare_calls(str(p)) == [("ws_query", 3)]


def test_intentional_single_shots_still_exist():
    for fn, func in INTENTIONAL_SINGLE_SHOT:
        assert func in {f for f, _ in _bare_calls(os.path.join(ROOT, fn))}, \
            f"{fn}::{func} no longer single-shot -- drop it from the allowlist"


class _Resp:
    def __init__(self, code, headers=None):
        self.status_code = code
        self.headers = headers or {}


class _Session:
    def __init__(self, script):
        self.script = list(script)
        self.calls = 0

    def request(self, method, url, **kw):
        self.calls += 1
        x = self.script.pop(0)
        if isinstance(x, Exception):
            raise x
        return x


def _run(script, **kw):
    s = _Session(script)
    sleeps = []
    try:
        out = request_with_retry("POST", "http://x", session=s, sleep=sleeps.append, **kw)
    except Exception as e:  # noqa: BLE001
        out = e
    return out, s.calls, sleeps


def test_write_retries_connection_refused_then_succeeds():
    out, calls, sleeps = _run([requests.exceptions.ConnectionError("refused"), _Resp(200)])
    assert out.status_code == 200 and calls == 2 and sleeps == [1.0]


def test_write_does_not_retry_a_timeout_or_5xx():
    out, calls, _ = _run([requests.exceptions.Timeout("t"), _Resp(200)])
    assert isinstance(out, requests.exceptions.Timeout) and calls == 1
    out, calls, _ = _run([_Resp(503), _Resp(200)])
    assert out.status_code == 503 and calls == 1


def test_read_retries_5xx_429_and_timeout():
    out, calls, sleeps = _run([_Resp(503), _Resp(429, {"Retry-After": "7"}),
                               requests.exceptions.Timeout("t"), _Resp(200)],
                              idempotent=True, retries=4)
    assert out.status_code == 200 and calls == 4 and sleeps == [1.0, 7.0, 4.0]


def test_exhaustion_keeps_the_bare_call_contract():
    out, calls, _ = _run([requests.exceptions.ConnectionError("a")] * 3)
    assert isinstance(out, requests.exceptions.ConnectionError) and calls == 3
    out, calls, _ = _run([_Resp(502)] * 3, idempotent=True)
    assert out.status_code == 502 and calls == 3   # returned, so raise_for_status still fires


def test_retry_after_is_capped():
    out, _, sleeps = _run([_Resp(429, {"Retry-After": "9999"}), _Resp(200)], idempotent=True, max_wait=30.0)
    assert sleeps == [30.0]
