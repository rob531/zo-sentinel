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
