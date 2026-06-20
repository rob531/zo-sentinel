"""_load_zo_secrets substring matching: a key named by provider token (nvidia=,
gemini=) OR canonical (NVIDIA_API_KEY=) both resolve -- mirrors the keyed-shim
wrapper. Lets a key dropped in /root/.zo_secrets be picked up by any launch path."""
import pytest


def _ls():
    pytest.importorskip("fastapi"); pytest.importorskip("uvicorn")
    import ladder_shim
    return ladder_shim


def test_token_named_key_resolves(tmp_path, monkeypatch):
    ls = _ls()
    f = tmp_path / "secrets"
    f.write_text("minimax=mm-key\nnvidia=nvapi-xyz\n")
    for k in ("MINIMAX_API_KEY", "NVIDIA_API_KEY", "GEMINI_API_KEY"):
        monkeypatch.delenv(k, raising=False)
    import os
    monkeypatch.setattr(ls, "_load_zo_secrets", ls._load_zo_secrets)  # ensure attr
    ls._load_zo_secrets(str(f))
    assert os.environ.get("NVIDIA_API_KEY") == "nvapi-xyz"
    assert os.environ.get("MINIMAX_API_KEY") == "mm-key"


def test_canonical_named_key_also_resolves(tmp_path, monkeypatch):
    ls = _ls(); import os
    f = tmp_path / "secrets"; f.write_text("NVIDIA_API_KEY=nvapi-abc\n")
    monkeypatch.delenv("NVIDIA_API_KEY", raising=False)
    ls._load_zo_secrets(str(f))
    assert os.environ.get("NVIDIA_API_KEY") == "nvapi-abc"


def test_does_not_clobber_existing_env(tmp_path, monkeypatch):
    ls = _ls(); import os
    f = tmp_path / "secrets"; f.write_text("nvidia=fromfile\n")
    monkeypatch.setenv("NVIDIA_API_KEY", "fromenv")
    ls._load_zo_secrets(str(f))
    assert os.environ.get("NVIDIA_API_KEY") == "fromenv"


def test_missing_file_safe(tmp_path):
    ls = _ls()
    ls._load_zo_secrets(str(tmp_path / "nope"))  # no raise


def test_cerebras_misspelled_token_resolves(tmp_path, monkeypatch):
    """User stored CEREBUS_API__KEY (misspelled, double underscore); the 'cereb'
    substring token still maps it to the correctly-named CEREBRAS_API_KEY the SDK reads."""
    ls = _ls(); import os
    f = tmp_path / "secrets"; f.write_text("CEREBUS_API__KEY=csk-misspelled\n")
    monkeypatch.delenv("CEREBRAS_API_KEY", raising=False)
    ls._load_zo_secrets(str(f))
    assert os.environ.get("CEREBRAS_API_KEY") == "csk-misspelled"


def test_mistral_freeform_name_resolves(tmp_path, monkeypatch):
    """User stored the key as 'mistralkeyfree'; 'mistral' substring token maps it."""
    ls = _ls(); import os
    f = tmp_path / "secrets"; f.write_text("mistralkeyfree=msk-abc\n")
    monkeypatch.delenv("MISTRAL_API_KEY", raising=False)
    ls._load_zo_secrets(str(f))
    assert os.environ.get("MISTRAL_API_KEY") == "msk-abc"

def test_groq_freeform_name_resolves(tmp_path, monkeypatch):
    ls = _ls(); import os
    f = tmp_path / "secrets"; f.write_text("freegroq=gsk-xyz\n")
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    ls._load_zo_secrets(str(f))
    assert os.environ.get("GROQ_API_KEY") == "gsk-xyz"
