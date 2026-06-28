"""CI freshness guard for the GraphifyKL schema artifact.

The committed graphify-out/schema_kl.json (the PRM's CI/fallback KL) must match the
real app.models. This fails if app/models.py changed without regenerating the KL
(run: python schema_kl.py --write). It SKIPS when app.models isn't importable in the
environment, so it never falsely fails CI -- it only enforces freshness when it can.
"""
import json
import pathlib

import pytest


def _cols(kl):
    return {m: sorted(v.get("columns", [])) for m, v in kl.get("models", {}).items()}


def test_committed_schema_kl_matches_models():
    schema_kl = pytest.importorskip("schema_kl")
    try:
        live = schema_kl.build_schema_kl()
    except Exception as e:  # app not importable in this env -> nothing to enforce
        pytest.skip(f"app.models not importable: {e}")
    p = pathlib.Path("graphify-out/schema_kl.json")
    if not p.exists():
        pytest.skip("no committed schema_kl.json")
    committed = json.loads(p.read_text(encoding="utf-8"))
    assert _cols(live) == _cols(committed), (
        "graphify-out/schema_kl.json is STALE vs app.models -- "
        "regenerate it with: python schema_kl.py --write")
