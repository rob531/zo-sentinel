"""Builder routes the validated module_from_exemplar lane to a live, tool-capable
coder rung (NVIDIA NIM -> Cerebras -> Mistral -> Groq), not the MiniMax floor that
hallucinates the schema. See build_routing.build_env_for."""
from zo_sentinel.build_routing import build_env_for


def test_exemplar_lane_routes_to_capable_rung():
    env = build_env_for({"recipe": "module_from_exemplar",
                         "complexity": "medium", "output_file": "x_api.py"})
    assert env["GOOSE_MODEL"] == "zo-ladder-nvidia", env


def test_non_exemplar_directive_unchanged():
    env = build_env_for({"complexity": "medium", "output_file": "x_api.py"})
    assert env["GOOSE_MODEL"] == "zo-ladder-medium", env
    env2 = build_env_for({"complexity": "low", "output_file": "y.py"})
    assert env2["GOOSE_MODEL"] == "zo-ladder-low", env2

def test_exemplar_lane_rotates_capable_rungs_by_attempt():
    # a quality failure (attempt>0) fails over to a different capable model
    window = ["zo-ladder-nvidia", "zo-ladder-mistral", "zo-ladder-cerebras", "zo-ladder-groq"]
    for attempt, expected in enumerate(window):
        env = build_env_for({"recipe": "module_from_exemplar", "complexity": "medium",
                             "output_file": "x_api.py"}, attempt=attempt)
        assert env["GOOSE_MODEL"] == expected, (attempt, env)


def test_webapp_recipes_route_to_capable_rung():
    # /app webapp-recipe builds must also reach the capable coder rung -- MiniMax-M3
    # (the medium pin) ignores the anti-hollow grounding and ships sqlite/declarative_base
    # stubs the no-hollow gate rejects.
    for recipe in ("webapp_backend_fastapi", "webapp_frontend_react", "webapp_fullstack"):
        env = build_env_for({"recipe": recipe, "complexity": "medium",
                             "output_file": "mcp_x_api.py"})
        assert env["GOOSE_MODEL"] == "zo-ladder-nvidia", (recipe, env)
