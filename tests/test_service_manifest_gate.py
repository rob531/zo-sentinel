"""Negative controls for the service-manifest shape gate (FU-120).

Every verdict this gate can return is asserted here, RED ones included. The
defect it exists to catch survived a "fail-closed" validator for two days
precisely because nobody had watched that validator refuse anything.

Run: python -m pytest tests/test_service_manifest_gate.py -q
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools"))

import check_service_manifests as C  # noqa: E402

CANONICAL = """[service]
name = "widget_report"
import_path = "services.active.widget_report.router"
prefix = "/api"
tag = "widget_report"
origin = "service"
auth = "public"
needs_data_layer = true
"""

# Verbatim from services/staged/registry_browse/service.toml as the builder emitted
# it in PR #2372 -- flat keys, a Python bool, and a __main__ block in a data file.
PYTHON_SHAPE = """name = "widget_report"
import_path = "services.active.widget_report.router"
prefix = "/api"
tag = "widget_report"
origin = "service"
auth = "public"
needs_data_layer = True

if __name__ == "__main__":
    print("PASS")
"""

# Verbatim shape from PR #2396 -- parses fine, but no [service] table.
FLAT_SHAPE = """name = "widget_report"
import_path = "services.active.widget_report.router"
prefix = "/api"
tag = "widget_report"
origin = "service"
auth = "public"
needs_data_layer = true
"""

DICT_SHAPE = """service = {
    "name": "widget_report",
    "import_path": "services.active.widget_report.router",
    "prefix": "/api",
    "tag": "widget_report",
    "needs_data_layer": True,
}
"""

# The nine origin="live" pre-SOA routers: prefix deliberately empty because the
# router object carries its own. An earlier draft of this gate demanded non-empty
# and would have rewritten these to "/api", moving nine live prod routes.
LIVE_EMPTY_PREFIX = """[service]
name = "widget_report"
import_path = "app.routers.widget_report"
prefix = ""
tag = ""
origin = "live"
auth = "public"
needs_data_layer = false
"""

# A manifest that is well-formed and confidently wrong: `name` was scraped out of a
# FastAPI seed row (`name="Test Org"`) in a file that had a whole router pasted in.
POISONED_NAME = """[service]
name = "Test Org"
import_path = "services.active.Test Org.router"
prefix = "/api"
tag = "widget_report"
origin = "service"
auth = "public"
needs_data_layer = true
"""


def _write(body, dirname="widget_report"):
    tmp = tempfile.mkdtemp()
    d = os.path.join(tmp, "services", "staged", dirname)
    os.makedirs(d)
    p = os.path.join(d, "service.toml")
    with open(p, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(body)
    return p


def test_canonical_is_green():
    assert C.classify(_write(CANONICAL))[0] == "OK"


def test_live_router_empty_prefix_is_green():
    """prefix = "" is legitimate; the gate must encode the promoter's contract."""
    assert C.classify(_write(LIVE_EMPTY_PREFIX))[0] == "OK"


def test_python_in_toml_is_red():
    assert C.classify(_write(PYTHON_SHAPE))[0] == "UNPARSEABLE"


def test_dict_literal_is_red():
    assert C.classify(_write(DICT_SHAPE))[0] == "UNPARSEABLE"


def test_flat_no_service_table_is_red():
    """The expensive one: valid TOML, so a syntax check alone calls it green."""
    verdict, detail = C.classify(_write(FLAT_SHAPE))
    assert verdict == "FLAT"
    assert "[service]" in detail


def test_poisoned_name_is_red():
    """Non-empty is not enough. "Test Org" trips the identifier rule first, which is
    why BAD_NAME and not NAME_MISMATCH -- both are red, and the ordering is the point:
    a value that cannot be a module path is rejected before it is compared."""
    assert C.classify(_write(POISONED_NAME))[0] == "BAD_NAME"


def test_valid_identifier_for_the_wrong_service_is_red():
    """The subtler case: a perfectly legal name that belongs to a DIFFERENT service --
    the copy-paste defect. Only the directory can settle it."""
    body = CANONICAL.replace('name = "widget_report"', 'name = "gadget_report"')
    verdict, detail = C.classify(_write(body, dirname="widget_report"))
    assert verdict == "NAME_MISMATCH"
    assert "widget_report" in detail


def test_import_path_that_is_not_a_module_path_is_red():
    body = CANONICAL.replace(
        'import_path = "services.active.widget_report.router"',
        'import_path = "services.active.Test Org.router"',
    )
    assert C.classify(_write(body))[0] == "BAD_IMPORT_PATH"


def test_missing_import_path_is_red():
    body = CANONICAL.replace('import_path = "services.active.widget_report.router"\n', "")
    assert C.classify(_write(body))[0] == "MISSING_KEYS"


def test_absent_prefix_key_is_red():
    body = CANONICAL.replace('prefix = "/api"\n', "")
    verdict, detail = C.classify(_write(body))
    assert verdict == "MISSING_KEYS"
    assert "prefix" in detail


def test_repair_never_trusts_the_broken_file_for_identity():
    """render() takes identity from the DIRECTORY, so scraped junk cannot survive."""
    p = _write(POISONED_NAME)
    out = C.render(C._harvest(p), "widget_report")
    assert 'name = "widget_report"' in out
    assert "Test Org" not in out


def test_repair_makes_every_bad_shape_green():
    for body in (PYTHON_SHAPE, DICT_SHAPE, FLAT_SHAPE, POISONED_NAME):
        p = _write(body)
        assert C.classify(p)[0] != "OK"          # red first -- prove the control
        with open(p, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(C.render(C._harvest(p), "widget_report"))
        assert C.classify(p)[0] == "OK"


def test_repair_preserves_a_nondefault_prefix():
    """Reshape, don't regenerate: the builder's real intent must survive."""
    body = FLAT_SHAPE.replace('prefix = "/api"', 'prefix = "/api/v2"')
    p = _write(body)
    out = C.render(C._harvest(p), "widget_report")
    assert 'prefix = "/api/v2"' in out
