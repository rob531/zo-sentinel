# deps: requests
"""Pure utility module for reading enrichment records from mcp_signal_enrichments."""

import requests


def read_enrichments(server_name: str, signal_type: str | None = None) -> list[dict]:
    """
    Read enrichment records for a given server from mcp_signal_enrichments.

    Args:
        server_name: The server identifier to filter enrichments by.
        signal_type: Optional signal type filter.

    Returns:
        List of dicts representing enrichment rows with all columns.
    """
    params: list = [server_name]
    sql = "SELECT id, server_id, signal_type, dimension, score, evidence_blob, computed_at, expires_at FROM mcp_signal_enrichments WHERE server_id = ?"
    if signal_type is not None:
        sql += " AND signal_type = ?"
        params.append(signal_type)

    resp = requests.post(
        "http://127.0.0.1:8772/query",
        json={"sql": sql, "params": params},
        timeout=10,
    )
    resp.raise_for_status()
    data = resp.json()
    return data.get("rows", [])


if __name__ == "__main__":
    result = read_enrichments("test-server")
    assert isinstance(result, list), f"Expected list, got {type(result)}"
    print("PASS")
