"""
Utility module that validates ServiceNow OAuth2 environment variables required
by the snow_connector Phase 9 integration.

PURPOSE: ensures SNOW_INSTANCE_URL, SNOW_CLIENT_ID, SNOW_CLIENT_SECRET are
present and non-empty before any SNOW daemon starts; emits a structured
validation dict.

INTERFACE: validate_snow_env() -> dict
    keys: instance_url, client_id, client_secret, valid, missing, error

CONSTRAINTS: stdlib-only (os), no DB writes, no network, no imports of
protected modules.

ACCEPTANCE: __main__ self-test asserts validate_snow_env() returns a dict
with 'valid' in keys, prints PASS, exits 0.
"""

import os


def validate_snow_env() -> dict:
    """
    Validate ServiceNow OAuth2 environment variables.

    Reads os.environ for SNOW_INSTANCE_URL, SNOW_CLIENT_ID, SNOW_CLIENT_SECRET.

    Returns:
        dict with keys:
            - instance_url:   str           -- value from env, or ''
            - client_id:      str           -- value from env, or ''
            - client_secret:  str           -- value from env, or ''
            - valid:          bool          -- True only when all three are non-empty
            - missing:        list[str]     -- names of missing/empty vars
            - error:          str | None    -- None when valid, else descriptive message
    """
    instance_url  = os.environ.get("SNOW_INSTANCE_URL",  "")
    client_id     = os.environ.get("SNOW_CLIENT_ID",     "")
    client_secret = os.environ.get("SNOW_CLIENT_SECRET", "")

    missing: list[str] = []
    if not instance_url:
        missing.append("SNOW_INSTANCE_URL")
    if not client_id:
        missing.append("SNOW_CLIENT_ID")
    if not client_secret:
        missing.append("SNOW_CLIENT_SECRET")

    valid = len(missing) == 0
    error: str | None = None if valid else f"missing required env vars: {', '.join(missing)}"

    return {
        "instance_url":   instance_url,
        "client_id":      client_id,
        "client_secret":  client_secret,
        "valid":          valid,
        "missing":        missing,
        "error":          error,
    }


if __name__ == "__main__":
    # Self-test: exercise validate_snow_env() against known inputs.

    # Case 1: all present -> valid=True, missing=[]
    os.environ["SNOW_INSTANCE_URL"]  = "https://example.service-now.com"
    os.environ["SNOW_CLIENT_ID"]     = "my-client-id"
    os.environ["SNOW_CLIENT_SECRET"] = "my-client-secret"

    result = validate_snow_env()
    assert isinstance(result, dict),      "must return a dict"
    assert "valid" in result,             "'valid' key must be present"
    assert result["valid"] is True,       "all vars present → valid=True"
    assert result["missing"] == [],       "no missing vars"
    assert result["error"] is None,       "no error when valid"
    assert result["instance_url"]   == "https://example.service-now.com"
    assert result["client_id"]      == "my-client-id"
    assert result["client_secret"]  == "my-client-secret"

    # Case 2: one missing -> valid=False, missing contains SNOW_CLIENT_ID
    del os.environ["SNOW_CLIENT_ID"]

    result = validate_snow_env()
    assert result["valid"] is False,      "missing var → valid=False"
    assert "SNOW_CLIENT_ID" in result["missing"]

    # Case 3: all missing -> valid=False, 3 items in missing
    os.environ.pop("SNOW_INSTANCE_URL",  None)
    os.environ.pop("SNOW_CLIENT_SECRET", None)

    result = validate_snow_env()
    assert result["valid"] is False,       "all vars missing → valid=False"
    assert len(result["missing"]) == 3,    "exactly 3 missing vars"
    assert isinstance(result["error"], str)

    # Case 4: empty string (set but blank) -> treated as missing
    os.environ["SNOW_INSTANCE_URL"] = ""
    os.environ["SNOW_CLIENT_ID"]     = ""
    os.environ["SNOW_CLIENT_SECRET"] = "my-secret"   # restore one

    result = validate_snow_env()
    assert result["valid"] is False,       "empty strings → invalid"
    assert "SNOW_INSTANCE_URL" in result["missing"]
    assert "SNOW_CLIENT_ID"     in result["missing"]
    assert "SNOW_CLIENT_SECRET" not in result["missing"]

    print("PASS")