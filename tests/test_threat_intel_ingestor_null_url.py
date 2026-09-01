"""FU-187 -- the NULL-url crash that kept threat_intel_ingestor down for 54 days.

Three separable defects, one assertion each, and for each one the NEGATIVE
CONTROL that proves the assertion goes RED against the pre-fix source. Without
those controls these would be assertions never seen red, which this repo's
ledger counts as unproven rather than passing.

The module's only import-time side effect is an os.makedirs() on a hardcoded
/home/workspace path, which is not writable on a CI runner -- so the loader
below neutralises makedirs for the duration of module execution. Nothing else
at import time touches the network or the filesystem.
"""

import contextlib
import importlib.util
import io
import os
import re
import types

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(REPO, "threat_intel_ingestor.py")


@contextlib.contextmanager
def _no_makedirs():
    real = os.makedirs
    os.makedirs = lambda *a, **k: None
    try:
        yield
    finally:
        os.makedirs = real


def _load(src_text=None, name="tii_under_test"):
    """Import threat_intel_ingestor, optionally from mutated source text."""
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


# ------------------------------------------------------- defect 1+2: the raise

def test_extract_package_name_tolerates_none(tii):
    """The exact call that raised in prod: a SQL NULL url reaching re.search."""
    assert tii.extract_package_name(None) is None


@pytest.mark.parametrize("bad", [None, 0, b"", [], {}, ""])
def test_extract_package_name_tolerates_any_non_string(tii, bad):
    assert tii.extract_package_name(bad) is None


def test_extract_package_name_still_extracts(tii):
    """Guarding the boundary must not break the happy path."""
    assert tii.extract_package_name("https://npmjs.com/package/left-pad") == "left-pad"
    assert tii.extract_package_name("https://pypi.org/project/requests") == "requests"
    assert tii.extract_package_name("https://github.com/foo/bar") == "bar"
    assert tii.extract_package_name("https://example.com/nothing-here") is None


def test_negctl_prefix_extract_package_name_raises(src):
    """NEGATIVE CONTROL: strip the guard, the TypeError comes back."""
    prefix_src = src.replace(
        "    if not isinstance(url, str) or not url:\n        return None\n", "", 1
    )
    assert prefix_src != src, "guard anchor not found -- test is not measuring the fix"
    prefix = _load(prefix_src, "tii_prefix_1")
    with pytest.raises(TypeError, match="expected string or bytes-like object"):
        prefix.extract_package_name(None)


# ------------------------------------------- defect 1: the .get() default trap

def test_the_get_default_does_not_apply_to_a_present_none():
    """The trap itself, stated as an assertion so nobody re-introduces it.

    `server.get('url', '')` looks defensive. It substitutes the default only
    when the KEY IS ABSENT -- never when the key is present carrying a SQL NULL.
    """
    assert {"url": None}.get("url", "") is None
    assert {}.get("url", "") == ""


def test_process_osv_vulns_survives_a_null_url_row(tii, monkeypatch):
    calls = []
    monkeypatch.setattr(tii, "get_mcp_servers_for_osv_scan", lambda: [
        {"server_id": "s1", "name": "a", "url": None},                    # SQL NULL
        {"server_id": "s2", "name": "b"},                                  # key absent
        {"server_id": "s3", "name": "c", "url": "https://npmjs.com/package/ok"},
    ])
    monkeypatch.setattr(tii, "query_osv", lambda eco, pkg: calls.append(pkg) or [])
    monkeypatch.setattr(tii, "log", lambda *a, **k: None)
    monkeypatch.setattr(tii, "ws_write", lambda *a, **k: None)

    assert tii.process_osv_vulns() == 0        # must not raise
    assert calls, "the one scannable row was never reached"
    assert set(calls) == {"ok"}


def test_negctl_prefix_process_osv_vulns_raises(src, monkeypatch):
    """NEGATIVE CONTROL: pre-fix, the same row set kills process_osv_vulns."""
    prefix_src = src.replace(
        "    if not isinstance(url, str) or not url:\n        return None\n", "", 1
    ).replace("url = server.get('url') or ''", "url = server.get('url', '')", 1)
    assert prefix_src != src
    prefix = _load(prefix_src, "tii_prefix_2")
    fetched = []

    def _stub():
        fetched.append(1)
        return [{"server_id": "s1", "name": "a", "url": None}]

    prefix.get_mcp_servers_for_osv_scan = _stub
    prefix.query_osv = lambda eco, pkg: []
    prefix.log = lambda *a, **k: None
    prefix.ws_write = lambda *a, **k: None
    with pytest.raises(TypeError):
        prefix.process_osv_vulns()
    # Guard the control itself: if the stub was never used, the raise came from
    # the real write_service/network path and proves nothing about the fix.
    assert fetched == [1], "negative control did not exercise the stubbed rows"


# ------------------------------- defect 3: the unprotected priming cycle

def test_priming_cycle_is_inside_a_try(src):
    """Structural: the FIRST cycle() call must be guarded, like every later one."""
    before_loop = src.split("def run():", 1)[1].split("while True:", 1)[0]
    assert re.search(r"try:\s*\n\s+cycle\(\)", before_loop), (
        "priming cycle() is not wrapped in try/except -- a first-cycle "
        "exception is terminal and no supervisor restart can help"
    )
    assert not re.search(r"^\s{4}cycle\(\)\s*$", before_loop, re.M), (
        "a BARE priming cycle() is still present"
    )


def test_negctl_prefix_run_dies_on_a_raising_first_cycle(src):
    """NEGATIVE CONTROL, behavioural: pre-fix the exception escapes run().

    Post-fix, run() must swallow it and reach the poll loop -- time.sleep is
    the escape hatch that proves the loop was entered.
    """
    prefix_src = src.replace(
        "    try:\n        cycle()\n    except Exception as e:\n"
        "        log(f'Priming cycle error (continuing into poll loop): {e}')\n"
        "        traceback.print_exc()\n",
        "    cycle()\n",
        1,
    )
    assert prefix_src != src, "priming-cycle anchor not found"

    class ReachedPollLoop(Exception):
        pass

    def wire(mod):
        mod.check_single_instance = lambda: None
        mod.create_tables = lambda: None
        mod.send_heartbeat = lambda: None
        mod.log = lambda *a, **k: None

        def boom():
            raise TypeError("expected string or bytes-like object, got 'NoneType'")

        mod.cycle = boom

        def sleep(*_):
            raise ReachedPollLoop()

        mod.time = types.SimpleNamespace(sleep=sleep)
        return mod

    # PRE-FIX: the TypeError escapes run() -- the process dies, 184,818 times.
    with pytest.raises(TypeError):
        wire(_load(prefix_src, "tii_prefix_3")).run()

    # POST-FIX: run() survives the priming failure and reaches the poll loop.
    with pytest.raises(ReachedPollLoop):
        wire(_load(src, "tii_post_3")).run()
