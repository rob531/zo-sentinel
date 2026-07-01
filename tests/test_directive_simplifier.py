"""directive_simplifier pure-function tests (no ladder call, no filesystem writes)."""
import directive_simplifier as ds


def test_extract_and_build_subdirectives():
    llm = (
        "Sure, here is the plan:\n"
        "[\n"
        '  {"task": "mcp_registry_query_layer", "output_file": "mcp_registry_query_layer.py",'
        ' "description": "Read rows from mcp_server_registry via the app.db session."},\n'
        '  {"task": "mcp_registry_route", "description": "FastAPI GET /mcp/server_registry wiring."}\n'
        "]\nDone."
    )
    arr = ds._extract_json_array(llm)
    assert arr and len(arr) == 2
    subs = ds._build_subdirectives(arr, parent_task="build_mcp_server_registry_api")
    assert len(subs) == 2
    assert all(s["handler"] == "generate_file" for s in subs)
    assert all(s["complexity"] == "low" for s in subs)
    assert all(s["source"] == "directive_simplifier" for s in subs)
    assert all(s["parent_task"] == "build_mcp_server_registry_api" for s in subs)
    # second item had no output_file -> defaulted + .py suffix
    assert subs[1]["output_file"].endswith(".py")


def test_extract_handles_no_json():
    assert ds._extract_json_array("no array here") is None
    assert ds._extract_json_array("") is None
    assert ds._build_subdirectives(None, "x") == []


def test_build_skips_malformed_items():
    raw = [{"task": "ok_one", "description": "does a thing"}, {"task": ""}, "nope", {"description": "no task"}]
    subs = ds._build_subdirectives(raw, "parent")
    assert [s["task"] for s in subs] == ["ok_one"]


def test_decompose_prompt_mentions_contract():
    p = ds._decompose_prompt({"task": "t", "description": "d", "output_file": "t.py"})
    assert "app.db" in p
    assert "JSON array" in p
    assert "t.py" in p