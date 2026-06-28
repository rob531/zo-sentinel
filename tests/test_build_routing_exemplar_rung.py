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
