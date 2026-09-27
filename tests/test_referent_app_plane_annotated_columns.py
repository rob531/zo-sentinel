"""The app plane must read BOTH declarative forms, or it reports FAIL for UNKNOWN.

WHY THIS FILE EXISTS (gh#4080, measured 2026-09-22)

    referent_verify's whole thesis is that "I could not evaluate this" must be
    distinguishable from "this is fine", and that the distinction must block.
    Its own app plane broke that thesis in the other direction.

    load_catalog() walked each model class body for `ast.Assign` only -- the
    SQLAlchemy 1.x form `id = Column(Integer, ...)`. app/models.py is written in
    the 2.0 annotated form, `id: Mapped[int] = mapped_column(...)`, which the
    parser sees as `ast.AnnAssign`. Measured against the real app/models.py at
    c63858aa: the extractor captured 4 column names and silently dropped 121.

    The consequence is NOT a missing feature. A column the extractor never read
    is absent from the catalog, and a referent absent from the catalog is
    reported MISSING -- i.e. FAIL, "checked and it does not exist" -- when the
    honest verdict is UNKNOWN, "could not be checked". The column check was
    publishing the one substitution the tool was built to make impossible, and
    it was doing it inside the tool. It is also why the count GREW (115 -> 145
    over the month the issue was open): every new call site against a 2.0-style
    model minted a fresh false FAIL, so working the list down could never
    converge.

THE TWO POLES
    Neither test passes by accident:
      * test_annotated_columns_are_read fails on the pre-fix extractor (the
        Assign-only walk yields an empty column set for an annotated model).
      * test_dunder_config_is_not_a_column is the control in the other
        direction -- widening the walk must not start admitting
        `__table_args__` as a resolvable column name, which would be a false
        PASS traded for the false FAIL.
"""
import ast
import importlib.util
import pathlib
import textwrap

ROOT = pathlib.Path(__file__).resolve().parents[1]

MODELS_SRC = textwrap.dedent(
    '''
    from sqlalchemy.orm import Mapped, mapped_column
    from sqlalchemy import Column, Integer, String


    class AnnotatedOnly(Base):
        """SQLAlchemy 2.0 declarative style -- every column is an AnnAssign."""

        __tablename__ = "annotated_only"
        __table_args__ = {"extend_existing": True}

        id: Mapped[int] = mapped_column(primary_key=True)
        server_id: Mapped[str] = mapped_column(String(64))
        stars: Mapped[int] = mapped_column(default=0)


    class LegacyOnly(Base):
        """SQLAlchemy 1.x style -- every column is a plain Assign."""

        __tablename__ = "legacy_only"

        id = Column(Integer, primary_key=True)
        name = Column(String(64))


    class MixedStyle(Base):
        __tablename__ = "mixed_style"

        id = Column(Integer, primary_key=True)
        created_at: Mapped[str] = mapped_column()
    '''
)


def _rv():
    spec = importlib.util.spec_from_file_location(
        "referent_verify", ROOT / "tools" / "referent_verify.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _app_plane(monkeypatch, tmp_path, src=MODELS_SRC):
    """Run load_catalog()'s app plane over `src` and return {table: {cols}}."""
    rv = _rv()
    models = tmp_path / "models.py"
    models.write_text(src, encoding="utf-8")
    monkeypatch.setattr(rv, "MODELS", models)
    # Neutralise the other planes so the assertion is about this one only.
    monkeypatch.setattr(rv, "MIGRATIONS", tmp_path / "no_such_migrations_dir")
    bus = tmp_path / "bus_catalog.json"
    bus.write_text(
        '{"captured_at": "%s", "tables": {}}'
        % __import__("datetime").datetime.now(
            __import__("datetime").timezone.utc).isoformat(),
        encoding="utf-8")
    monkeypatch.setattr(rv, "BUS_CATALOG", bus)
    tables, meta, unknown = rv.load_catalog()
    assert unknown is None, f"catalog could not be built: {unknown}"
    return tables, meta


def test_annotated_columns_are_read(monkeypatch, tmp_path):
    """RED on the Assign-only extractor: an annotated model yielded no columns."""
    tables, meta = _app_plane(monkeypatch, tmp_path)

    assert "annotated_only" in tables, (
        "the table itself must still resolve -- __tablename__ is a plain Assign")
    assert tables["annotated_only"] >= {"id", "server_id", "stars"}, (
        "mapped_column() declarations are ast.AnnAssign; reading only ast.Assign "
        "leaves them out of the catalog, and a column the extractor never read "
        "is then reported MISSING -- FAIL standing in for UNKNOWN (gh#4080)")

    # The 1.x form must keep working -- this is a widening, not a swap.
    assert tables["legacy_only"] >= {"id", "name"}

    # And a class that mixes the two must yield both.
    assert tables["mixed_style"] >= {"id", "created_at"}

    # 3 (annotated_only) + 2 (legacy_only) + 2 (mixed_style) = 7, and the two
    # dunders must NOT be counted -- 9 here would mean __table_args__ and
    # __tablename__ were admitted, which the control below forbids.
    assert meta.get("app_model_columns", 0) == 7, (
        "the plane should now publish how many columns it actually captured, so "
        "a future regression to 4-of-125 is visible in the report rather than "
        "silently priced into the missing-column count")


def test_dunder_config_is_not_a_column(monkeypatch, tmp_path):
    """Control in the opposite direction: do not trade a false FAIL for a false PASS."""
    tables, _ = _app_plane(monkeypatch, tmp_path)
    assert "__table_args__" not in tables["annotated_only"], (
        "__table_args__ is mapper configuration. Admitting it would let a query "
        "naming it resolve against the catalog, which is a false PASS")
    assert "__tablename__" not in tables["annotated_only"]


def test_the_real_models_file_is_annotated_style(monkeypatch, tmp_path):
    """Guard the premise: if app/models.py stops being 2.0-style this fix is moot.

    Not a pass-by-construction assertion -- it reads the shipped file and would
    go red if the tree were migrated back to Column(), at which point the
    reasoning above needs revisiting rather than silently carrying on.
    """
    rv = _rv()
    if not rv.MODELS.exists():                       # pragma: no cover
        import pytest
        pytest.skip("app/models.py absent in this checkout")
    tree = ast.parse(rv.MODELS.read_text(encoding="utf-8"))
    ann = sum(1 for n in ast.walk(tree) if isinstance(n, ast.AnnAssign))
    assert ann > 0, (
        "app/models.py carries no annotated assignments at all; the premise of "
        "gh#4080's app-plane fix no longer holds")
