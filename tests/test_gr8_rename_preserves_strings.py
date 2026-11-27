"""GR-8 -- a model-name autofix must never rewrite string literals or comments.

Register row GR-8 (LOCO_CHAIRMAN.md 6.1): `model_import_linter --fix` corrupted SQL
table names inside string literals (#4000 / #4004) because the rename was a whole-file
`re.sub(r"\\bWrong\\b", canon, src)`. The linter was moved to a tokenizer splice; the
class census (every --fix/--apply tool that renames identifiers by regex) found one
sibling, `tools/repair_staged_model_names.py::apply_file`, with the identical line.

These tests pin BOTH fixers. Each fixture is checked to DISCRIMINATE first: the old
regex is run on it and must corrupt the string, otherwise a green here would prove
nothing (a fixture the defect cannot touch is a vacuous pass).

Census: `test_no_whole_file_identifier_regex_left_in_fixers` below walks every module
in tools/ and zo_sentinel/ by AST for the `re.sub("\\b%s\\b" % re.escape(..))` shape.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
import tempfile
import textwrap

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOOLS = os.path.join(ROOT, "tools")
sys.path.insert(0, TOOLS)

import model_import_linter as MIL  # noqa: E402
import repair_staged_model_names as RSM  # noqa: E402

FIXTURE = textwrap.dedent('''\
    """Docstring naming Users and McpServerRegistrys on purpose."""
    from app.models import Users, McpServerRegistrys

    SQL = "SELECT id FROM Users JOIN McpServerRegistrys ON 1=1"  # Users table
    RAW = r'McpServerRegistrys'


    def handler():
        return Users, McpServerRegistrys, SQL, RAW
    ''')
DRIFT = {"Users": "User", "McpServerRegistrys": "McpServerRegistry"}


def _old_regex(src: str, drift: dict) -> str:
    """The pre-fix line, verbatim in shape -- used only to prove the fixture bites."""
    for wrong, canon in drift.items():
        src = re.sub(r"\b%s\b" % re.escape(wrong), canon, src)
    return src


def _string_and_comment_bytes(src: str) -> list[str]:
    import io
    import tokenize
    keep = (tokenize.STRING, tokenize.COMMENT)
    return [t.string for t in tokenize.generate_tokens(io.StringIO(src).readline)
            if t.type in keep]


def test_fixture_discriminates():
    """Negative control: the defect really does corrupt this fixture."""
    bad = _old_regex(FIXTURE, DRIFT)
    assert _string_and_comment_bytes(bad) != _string_and_comment_bytes(FIXTURE)
    assert '"SELECT id FROM User JOIN McpServerRegistry ON 1=1"' in bad


def _assert_fixed_and_preserved(out: str):
    assert _string_and_comment_bytes(out) == _string_and_comment_bytes(FIXTURE), \
        "a string literal / docstring / comment was rewritten (GR-8)"
    assert "from app.models import User, McpServerRegistry\n" in out
    assert "return User, McpServerRegistry, SQL, RAW" in out


def test_linter_substitution_is_code_only():
    _assert_fixed_and_preserved(MIL._substitute_names(FIXTURE, DRIFT))


def test_linter_fails_safe_on_untokenizable_source():
    broken = FIXTURE + '\nX = """unterminated\n'
    assert MIL._substitute_names(broken, DRIFT) == broken


def test_repair_staged_apply_file_is_code_only():
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "router.py")
        with open(p, "w", encoding="utf-8", newline="") as f:
            f.write(FIXTURE)
        assert RSM.apply_file(p, dict(DRIFT), []) is True
        with open(p, encoding="utf-8") as f:
            _assert_fixed_and_preserved(f.read())
        # idempotent: a second apply finds nothing to change
        assert RSM.apply_file(p, dict(DRIFT), []) is False


def test_linter_fix_cli_preserves_sql_literal():
    """End to end through `--fix`, against the real app/models.py canon."""
    canon = MIL.canonical_models()
    if "McpServerRegistry" not in canon:  # pragma: no cover - schema moved
        raise AssertionError("McpServerRegistry not in app/models.py canon; update fixture")
    src = ('from app.models import MCPServerRegistry\n'
           'Q = "SELECT * FROM MCPServerRegistry"\n'
           'def f():\n    return MCPServerRegistry, Q\n')
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "svc.py")
        with open(p, "w", encoding="utf-8", newline="") as f:
            f.write(src)
        subprocess.run([sys.executable, os.path.join(TOOLS, "model_import_linter.py"), "--fix", p],
                       capture_output=True, text=True, timeout=60)
        with open(p, encoding="utf-8") as f:
            out = f.read()
    assert 'Q = "SELECT * FROM MCPServerRegistry"' in out
    assert "from app.models import McpServerRegistry\n" in out


def _bare_identifier_regex_subs(path: str) -> list[int]:
    """Lines where CODE (not a docstring/comment) calls re.sub with a pattern built
    as `"\\b%s\\b" % re.escape(...)` -- the GR-8 shape. AST, so quoting the old line
    in a docstring (as model_import_linter does) is not a hit."""
    import ast
    with open(path, encoding="utf-8", errors="replace") as f:
        try:
            tree = ast.parse(f.read())
        except SyntaxError:
            return []
    out = []
    for n in ast.walk(tree):
        if not (isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                and n.func.attr == "sub" and isinstance(n.func.value, ast.Name)
                and n.func.value.id == "re" and n.args):
            continue
        a = n.args[0]
        if (isinstance(a, ast.BinOp) and isinstance(a.op, ast.Mod)
                and isinstance(a.left, ast.Constant) and isinstance(a.left.value, str)
                and a.left.value.startswith("\\b") and "%s" in a.left.value):
            out.append(n.lineno)
    return out


def test_census_detector_sees_the_old_shape():
    """Negative control for the census: the detector flags the pre-fix line."""
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "old.py")
        with open(p, "w", encoding="utf-8") as f:
            f.write('import re\nnew = re.sub(r"\\b%s\\b" % re.escape(w), c, new)\n')
        assert _bare_identifier_regex_subs(p) == [2]


def test_no_whole_file_identifier_regex_left_in_fixers():
    """The census, executed to zero across tools/ and zo_sentinel/."""
    hits = []
    for base in ("tools", "zo_sentinel"):
        for fn in sorted(os.listdir(os.path.join(ROOT, base))):
            if fn.endswith(".py"):
                for ln in _bare_identifier_regex_subs(os.path.join(ROOT, base, fn)):
                    hits.append(f"{base}/{fn}:{ln}")
    assert hits == [], "GR-8 class regression:\n" + "\n".join(hits)


# --- GR-8 detection half (2026-11-27) -------------------------------------------
# The fix half above was code-only; the DETECTION half still read raw text. Two
# consequences, both measured on services/staged before this change:
#   * scan_text reported 673 "wrong refs" in 503 files -- table names in docstrings
#     and SQL -- that --fix (correctly) could never clear, so --check stayed red,
#     promote_staged_to_active recorded casing_fixed renames it never made, and
#     orphanage labelled services BROKEN_IMPORT "--fix likely revives". After: 0.
#   * scan_imports read the trailing comment of `from app.models import User  # the
#     user model`, mapped `user` -> User, and --fix then renamed every NAME `user`
#     (parameters, locals) to the model class: `def f(user)` became `def f(User)`.

DETECT_FIXTURE = textwrap.dedent('''\
    from app.models import User  # the user model
    Q = "SELECT * FROM mcp_server_registry"
    """Writes mcp_server_registry.risk_tier."""


    def f(user):
        return User, user, Q
    ''')


def test_scan_text_ignores_strings_and_docstrings():
    nm = MIL.build_map(MIL.canonical_models())
    assert "McpServerRegistry" in nm.values()
    assert MIL.scan_text(DETECT_FIXTURE, nm) == {}
    # positive control: the same name as CODE is still caught
    assert MIL.scan_text("x = mcp_server_registry\n", nm) == {"mcp_server_registry": "McpServerRegistry"}


def test_scan_text_untokenizable_still_reports():
    nm = MIL.build_map(MIL.canonical_models())
    broken = 'x = mcp_server_registry\ny = """unterminated\n'
    assert "mcp_server_registry" in MIL.scan_text(broken, nm)


def test_import_comment_does_not_rename_a_local():
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "svc.py")
        with open(p, "w", encoding="utf-8", newline="") as f:
            f.write(DETECT_FIXTURE)
        r = subprocess.run([sys.executable, os.path.join(TOOLS, "model_import_linter.py"), "--fix", p],
                           capture_output=True, text=True, timeout=60)
        with open(p, encoding="utf-8") as f:
            out = f.read()
    assert out == DETECT_FIXTURE, "--fix rewrote a file with no real drift:\n" + out
    assert r.returncode == 0, r.stdout + r.stderr


def test_import_scan_still_catches_real_drift():
    full = MIL.build_map(MIL.all_models())
    got = MIL.scan_imports("from app.models import Users  # users\n", full)
    assert got == {"Users": "User"}, got
