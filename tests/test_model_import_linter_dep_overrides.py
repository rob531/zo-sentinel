"""MERGE_AUDIT_2026-08-23 L1: the `app.dependency_overrides` cluster.

`app.dependency_overrides` is not a module -- `app` is the package and
dependency_overrides is an attribute of the FastAPI instance, re-exported by
app/__init__.py. 15 sites across services/staged/ carried the bad form, the
largest single unresolved-import cluster in the tree, despite the goose recipe
warning about it in prose. Prose in the prompt did not stop it; this rewrite is
mechanical.

The safety property under test is the one that matters: a name whose real home
is UNKNOWN is never rewritten, because binding it to `dependency_overrides`
would trade a loud ImportError for a silent NameError at the call site.
"""

from tools import model_import_linter as mil


def _rewrites(src):
    return mil.scan_dependency_overrides(src)[0]


def _unfixable(src):
    return mil.scan_dependency_overrides(src)[1]


def test_dependency_overrides_itself_rewrites_to_the_package():
    src = "from app.dependency_overrides import dependency_overrides\n"
    (old, new), = _rewrites(src)
    assert old.strip() == "from app.dependency_overrides import dependency_overrides"
    assert new == "from app import dependency_overrides"


def test_get_session_is_pointed_at_its_real_home():
    """get_session is defined in app/db.py, not on the app instance."""
    (_old, new), = _rewrites("from app.dependency_overrides import get_session\n")
    assert new == "from app.db import get_session"


def test_alias_is_preserved():
    src = "from app.dependency_overrides import get_session as override_get_session\n"
    (_old, new), = _rewrites(src)
    assert new == "from app.db import get_session as override_get_session"


def test_app_is_pointed_at_app_main():
    (_old, new), = _rewrites("from app.dependency_overrides import app\n")
    assert new == "from app.main import app"


def test_indentation_is_preserved():
    """Most real sites sit inside `if __name__ == '__main__':` blocks."""
    (_old, new), = _rewrites("    from app.dependency_overrides import dependency_overrides\n")
    assert new == "    from app import dependency_overrides"


def test_trailing_comment_is_preserved():
    src = "from app.dependency_overrides import dependency_overrides  # note\n"
    (_old, new), = _rewrites(src)
    assert new == "from app import dependency_overrides  # note"


def test_unknown_name_is_never_rewritten():
    """The safety property: override_get_session exists nowhere under app/ and is
    CALLED at the site. Rewriting would turn ImportError into NameError."""
    src = "from app.dependency_overrides import override_get_session\n"
    assert _rewrites(src) == []
    stuck = _unfixable(src)
    assert len(stuck) == 1
    assert "override_get_session" in stuck[0][0]


def test_mixed_known_and_unknown_rewrites_nothing():
    """One unknown name poisons the statement -- partial rewrites would drop a
    binding the file still uses."""
    src = "from app.dependency_overrides import dependency_overrides, made_up_helper\n"
    assert _rewrites(src) == []
    assert _unfixable(src)


def test_several_known_names_split_by_home():
    src = "from app.dependency_overrides import dependency_overrides, get_session\n"
    (_old, new), = _rewrites(src)
    assert new == "from app import dependency_overrides\nfrom app.db import get_session"


def test_correct_attribute_usage_is_untouched():
    """`app.dependency_overrides[get_session] = ...` is CORRECT and far more
    common than the broken import. It must never be rewritten."""
    src = "app.dependency_overrides[get_session] = lambda: db\n"
    assert _rewrites(src) == []
    assert _unfixable(src) == []


def test_lint_file_reports_both_keys(tmp_path):
    p = tmp_path / "svc.py"
    p.write_text("from app.dependency_overrides import dependency_overrides\n",
                 encoding="utf-8")
    res = mil.lint_file(str(p), mil.build_map(mil.canonical_models()), fix=False)
    assert res["dep_overrides"]
    assert res["dep_overrides_unfixable"] == []
    assert p.read_text(encoding="utf-8").startswith("from app.dependency_overrides")


def test_lint_file_fix_rewrites_in_place(tmp_path):
    p = tmp_path / "svc.py"
    p.write_text("    from app.dependency_overrides import dependency_overrides\n",
                 encoding="utf-8")
    res = mil.lint_file(str(p), mil.build_map(mil.canonical_models()), fix=True)
    assert res["fixed"] is True
    assert p.read_text(encoding="utf-8") == "    from app import dependency_overrides\n"
