"""Generic OpenAI-compatible rung adapter + NVIDIA wiring. Adding an
OpenAI-SDK-compatible provider becomes a LADDER config row, not new code."""
import escalation as E


def test_modelspec_carries_base_url_and_key_env():
    s = E.ModelSpec("openai_compatible", "m", 1, 1, 0.0, "l",
                    base_url="http://x/v1", key_env="K")
    assert s.base_url == "http://x/v1" and s.key_env == "K"
    # existing positional specs still construct (defaults)
    assert E.LADDER[0].base_url == "" and E.LADDER[0].key_env == ""


def test_nvidia_rung_wired_nonbreaking():
    idx = E.TASK_START_TIER["builder_nvidia"]
    sp = E.LADDER[idx]
    assert sp.backend == "openai_compatible"
    assert sp.key_env == "NVIDIA_API_KEY"
    # exact match (not a "host in url" substring check -- CodeQL flags those)
    assert sp.base_url == "https://integrate.api.nvidia.com/v1"
    assert E.MODEL_TASK_MAP["zo-ladder-nvidia"] == "builder_nvidia"
    assert E.task_for_model("zo-ladder-nvidia") == "builder_nvidia"
    assert "openai_compatible" in E.BACKEND_ADAPTERS
    # non-breaking: the original index-based tiers are unchanged
    assert E.TASK_START_TIER["builder_low"] == 0
    assert E.TASK_START_TIER["builder_critical"] == 15


def test_adapter_errors_clean_when_key_absent(monkeypatch):
    monkeypatch.delenv("NVIDIA_API_KEY", raising=False)
    sp = E.ModelSpec("openai_compatible", "m", 1, 256, 0.0, "l",
                     base_url="https://integrate.api.nvidia.com/v1", key_env="NVIDIA_API_KEY")
    text, err, tcs = E._call_openai_compatible(sp, "hi", None, 256, 0.0, None)
    assert text is None and tcs is None and "not set" in err  # no HTTP, ladder falls through


def test_adapter_reads_key_from_env(monkeypatch):
    monkeypatch.setenv("NVIDIA_API_KEY", "nvapi-test")
    sp = E.ModelSpec("openai_compatible", "m", 1, 256, 0.0, "l", base_url="", key_env="NVIDIA_API_KEY")
    text, err, tcs = E._call_openai_compatible(sp, "hi", None, 256, 0.0, None)
    assert "no base_url" in err  # got past the key check -> key was read


def test_cerebras_rung_wired_nonbreaking():
    idx = E.TASK_START_TIER["builder_cerebras"]
    sp = E.LADDER[idx]
    assert sp.backend == "openai_compatible"
    assert sp.key_env == "CEREBRAS_API_KEY"
    assert sp.base_url == "https://api.cerebras.ai/v1"   # exact (CodeQL-safe)
    assert E.MODEL_TASK_MAP["zo-ladder-cerebras"] == "builder_cerebras"
    assert E.task_for_model("zo-ladder-cerebras") == "builder_cerebras"
    # NVIDIA rung refreshed off the EOL model
    assert "qwen2.5-coder-32b" not in E.LADDER[E.TASK_START_TIER["builder_nvidia"]].model_id
    # non-breaking: original tiers unchanged
    assert E.TASK_START_TIER["builder_low"] == 0
    assert E.TASK_START_TIER["builder_critical"] == 15


def test_adapter_sends_user_agent_and_tuning(monkeypatch):
    """Cerebras (Cloudflare) 403s default python-requests UA; gpt-oss wants
    reasoning_effort. Adapter must send a browser UA + merge spec.extra_params."""
    import requests
    captured = {}

    class _Resp:
        status_code = 200
        def raise_for_status(self): pass
        def json(self):
            return {"choices": [{"message": {"content": "",
                    "tool_calls": [{"function": {"name": "write_file", "arguments": "{}"}}]}}]}

    def _fake_post(url, headers=None, json=None, timeout=None):
        captured["url"] = url; captured["headers"] = headers; captured["json"] = json
        return _Resp()

    monkeypatch.setattr(requests, "post", _fake_post)
    monkeypatch.setenv("CEREBRAS_API_KEY", "csk-test")
    sp = E.ModelSpec("openai_compatible", "gpt-oss-120b", 131072, 30, 0.2, "cere",
                     base_url="https://api.cerebras.ai/v1", key_env="CEREBRAS_API_KEY",
                     extra_params={"reasoning_effort": "medium"})
    text, err, tcs = E._call_openai_compatible(sp, "hi", None, 256, 0.2, E_TOOLS)
    assert "Mozilla/" in captured["headers"]["User-Agent"]          # not python-requests
    assert captured["json"]["reasoning_effort"] == "medium"          # SDK tuning forwarded
    assert captured["json"]["tools"] == E_TOOLS                      # tools still forwarded
    assert tcs and tcs[0]["function"]["name"] == "write_file"


def test_cerebras_rung_carries_reasoning_effort():
    sp = E.LADDER[E.TASK_START_TIER["builder_cerebras"]]
    assert sp.extra_params.get("reasoning_effort") == "medium"


def test_nvidia_model_is_live_tool_built():
    sp = E.LADDER[E.TASK_START_TIER["builder_nvidia"]]
    assert "nemotron" in sp.model_id          # mistral-nemotron (built for tool calling)
    assert "qwen2.5-coder-32b" not in sp.model_id and "qwen3-coder-480b" not in sp.model_id


E_TOOLS = [{"type": "function", "function": {"name": "write_file",
            "parameters": {"type": "object", "properties": {}}}}]


def test_mistral_rung_wired_nonbreaking():
    idx = E.TASK_START_TIER["builder_mistral"]
    sp = E.LADDER[idx]
    assert sp.backend == "openai_compatible"
    assert sp.key_env == "MISTRAL_API_KEY"
    assert sp.base_url == "https://api.mistral.ai/v1"   # exact (CodeQL-safe)
    assert E.MODEL_TASK_MAP["zo-ladder-mistral"] == "builder_mistral"
    assert E.task_for_model("zo-ladder-mistral") == "builder_mistral"
    # all three capacity rungs distinct + non-breaking
    assert len({E.TASK_START_TIER[k] for k in ("builder_nvidia","builder_cerebras","builder_mistral")}) == 3
    assert E.TASK_START_TIER["builder_critical"] == 15
