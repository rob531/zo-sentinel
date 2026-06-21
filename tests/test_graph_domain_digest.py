"""graph_domain_digest: pure digest formatting (stdlib only, imports in CI)."""
import importlib.util, pathlib
_spec = importlib.util.spec_from_file_location(
    "gdd", pathlib.Path(__file__).resolve().parents[1] / "tools" / "graph_domain_digest.py")
GD = importlib.util.module_from_spec(_spec); _spec.loader.exec_module(GD)


def test_format_digest_has_domains_and_parked():
    rows = [{"community": 2, "modules": 131, "example": "execution_api_service.py"},
            {"community": 4, "modules": 99, "example": "api_gateway.py"}]
    d = GD.format_digest(rows)
    assert "domain 2: 131 modules" in d and "api_gateway.py" in d
    assert "RANGE across" in d
    assert "PARKED" in d and "snow_connector" in d and "aidr_commit_gateway" in d


def test_format_digest_empty_safe():
    d = GD.format_digest([])
    assert "DOMAIN MAP" in d and "PARKED" in d   # never crashes on empty graph
