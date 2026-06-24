"""Unit tests for goose_runner._select_recipe -- the per-directive recipe selector
that routes app-spine modules to the webapp recipes (else architect.yaml).
Imports goose_runner standalone (guarded __main__, no fastapi/network at import)."""
import goose_runner as g


def test_auth_spine_routes_to_backend_fastapi():
    for did in ("build_oauth_login_service", "build_rbac_enforcer",
                "build_tenant_org_model", "build_org_session_auth"):
        assert g._select_recipe({"directive_id": did}) == "webapp_backend_fastapi", did


def test_html_and_dashboard_view_route_to_frontend():
    assert g._select_recipe({"directive_id": "build_overview_dashboard",
                             "output_file": "app/overview_dashboard.html"}) == "webapp_frontend_react"
    assert g._select_recipe({"directive_id": "build_org_ui_view",
                             "output_file": "frontend/org_view.html"}) == "webapp_frontend_react"


def test_enricher_and_signal_stay_on_architect_default():
    for did in ("build_signal_enricher", "build_domain_provenance_signal",
                "wire_risk_ranker", "build_mcp_discovery_feeder"):
        assert g._select_recipe({"directive_id": did}) is None, did


def test_explicit_recipe_field_wins_when_allowlisted():
    assert g._select_recipe({"directive_id": "anything", "recipe": "webapp_frontend_react"}) == "webapp_frontend_react"
    assert g._select_recipe({"directive_id": "x", "recipe": "webapp_backend_fastapi"}) == "webapp_backend_fastapi"


def test_explicit_architect_or_bogus_recipe_falls_through_to_inference():
    # "architect" is allowlisted but means default -> not returned as override
    assert g._select_recipe({"directive_id": "build_signal_enricher", "recipe": "architect"}) is None
    # a bogus recipe name is ignored, inference still applies
    assert g._select_recipe({"directive_id": "build_oauth_login_service", "recipe": "nope"}) == "webapp_backend_fastapi"


def test_recipe_files_exist_and_parse():
    import yaml, pathlib
    base = pathlib.Path(__file__).resolve().parents[1] / "goose_recipes"
    for name in ("webapp_backend_fastapi", "webapp_frontend_react", "webapp_fullstack"):
        d = yaml.safe_load(open(base / f"{name}.yaml", encoding="utf-8"))
        assert d.get("title") and d.get("prompt"), name
