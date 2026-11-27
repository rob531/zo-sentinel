"""GR-10: no `dependency_overrides[X]` keys a callable imported from a module that
does not exist (or that does not define X).

FastAPI overrides by identity: an override keyed on a phantom callable never
fires, and the import that names it crashes the module first. The 2026-08-29
census found 6 sites; 3 were the hallucinated `from app.dependency_overrides
import get_session` (fixed: get_session lives in app.db). The other 3 sit in
modules that are hollow end to end (nonexistent app.dependencies / app.schemas,
a staged service named after a dir with no source) -- they are KNOWN_HOLLOW
below, each must still exist (stale entries fail), and any NEW site is red.
"""
import ast
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SKIP_DIRS = {".git", "archive", "node_modules", ".venv", "__pycache__", "quarantine"}
LOCAL_ROOTS = ("app", "zo_sentinel", "services", "tools")

# (file, override key) -> why it is not fixed here. Owner: GR-23 (staging exit) for
# services/staged; the breaker action imports two app modules that were never written.
KNOWN_HOLLOW = {
    ("breaker_actions/breaker_action_reset_server_export_api.py", "app.dependencies.get_zo_computer_client"):
        "module imports app.dependencies + app.schemas, neither exists; whole file hollow",
    ("services/staged/trust_override_discrepancy/contract.py", "services.staged.trust_gating_override.contract.trust_gate"):
        "staged dir trust_gating_override has no source; trust_gate is not a dependency (GR-23)",
    ("services/staged/trust_override_discrepancy/logic.py", "services.staged.trust_gating_override.logic.trust_gate"):
        "same staged service (GR-23)",
}


def _mod_path(mod):
    p = os.path.join(ROOT, *mod.split("."))
    for c in (p + ".py", os.path.join(p, "__init__.py")):
        if os.path.exists(c):
            return c
    return None


def _defines(path, name):
    try:
        tree = ast.parse(open(path, encoding="utf-8", errors="replace").read())
    except SyntaxError:
        return True  # unparseable is another gate's finding, not this one
    for n in ast.walk(tree):
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and n.name == name:
            return True
        if isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id == name for t in n.targets):
            return True
        if isinstance(n, (ast.ImportFrom, ast.Import)):
            for a in n.names:
                if (a.asname or a.name.split(".")[0]) == name or a.name in (name, "*"):
                    return True
    return False


def phantom_sites(root=ROOT):
    out = set()
    for dp, dn, fns in os.walk(root):
        dn[:] = [d for d in dn if d not in SKIP_DIRS]
        for fn in fns:
            if not fn.endswith(".py"):
                continue
            p = os.path.join(dp, fn)
            src = open(p, encoding="utf-8", errors="replace").read()
            if "dependency_overrides" not in src:
                continue
            try:
                tree = ast.parse(src)
            except SyntaxError:
                continue
            imports = {}
            for n in ast.walk(tree):
                if isinstance(n, ast.ImportFrom) and n.module and n.level == 0:
                    for a in n.names:
                        imports[a.asname or a.name] = (n.module, a.name)
            for n in ast.walk(tree):
                if not isinstance(n, ast.Subscript):
                    continue
                v = n.value
                if not ((isinstance(v, ast.Attribute) and v.attr == "dependency_overrides")
                        or (isinstance(v, ast.Name) and v.id == "dependency_overrides")):
                    continue
                if isinstance(n.slice, ast.Name) and n.slice.id in imports:
                    mod, name = imports[n.slice.id]
                    mp = _mod_path(mod)
                    local = mod.split(".")[0] in LOCAL_ROOTS or os.path.exists(
                        os.path.join(root, mod.split(".")[0] + ".py"))
                    if (mp is None and local) or (mp is not None and not _defines(mp, name)):
                        out.add((os.path.relpath(p, root).replace(os.sep, "/"), f"{mod}.{name}"))
    return out


def test_no_new_phantom_override_sites():
    new = phantom_sites() - set(KNOWN_HOLLOW)
    assert not new, f"dependency_overrides keyed on a callable that exists nowhere: {sorted(new)}"


def test_known_hollow_entries_are_not_stale():
    stale = set(KNOWN_HOLLOW) - phantom_sites()
    assert not stale, f"fixed or removed -- drop from KNOWN_HOLLOW: {sorted(stale)}"


def test_detector_catches_the_original_shape(tmp_path):
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "db.py").write_text("def get_session():\n    pass\n")
    (tmp_path / "x.py").write_text(
        "from app.db import get_session\nif __name__ == '__main__':\n"
        "    from app.dependency_overrides import get_session\n"
        "    app.dependency_overrides[get_session] = lambda: 1\n")
    (tmp_path / "ok.py").write_text(
        "from app.db import get_session\napp.dependency_overrides[get_session] = lambda: 1\n")
    assert phantom_sites(str(tmp_path)) == {("x.py", "app.dependency_overrides.get_session")}
