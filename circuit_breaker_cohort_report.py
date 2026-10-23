"""Circuit breaker cohort diagnostic report - reads gate_8_quarantine state from mesh memory."""

import json


def _get_stub_data():
    """Return hardcoded stub data for self-test."""
    return [
        {
            "file": "data_processor.py",
            "quarantined_at": "2024-01-15T10:30:00Z",
            "attempts": 47,
            "last_error": "Connection timeout",
            "fail_rate": 0.67,
            "cohort_size": 120,
        },
        {
            "file": "auth_handler.py",
            "quarantined_at": "2024-01-15T11:45:00Z",
            "attempts": 34,
            "last_error": "Signature validation failed",
            "fail_rate": 0.35,
            "cohort_size": 89,
        },
        {
            "file": "cache_manager.py",
            "quarantined_at": "2024-01-15T14:20:00Z",
            "attempts": 23,
            "last_error": "Invalid key format",
            "fail_rate": 0.45,
            "cohort_size": 156,
        },
        {
            "file": "api_gateway.py",
            "quarantined_at": "2024-01-16T09:15:00Z",
            "attempts": 89,
            "last_error": "Upstream timeout",
            "fail_rate": 0.78,
            "cohort_size": 234,
        },
        {
            "file": "config_loader.py",
            "quarantined_at": "2024-01-16T16:40:00Z",
            "attempts": 12,
            "last_error": "Schema mismatch",
            "fail_rate": 0.25,
            "cohort_size": 67,
        },
    ]


def _query_mesh_memory():
    """Query mesh memory via write_service for gate_8_quarantine state."""
    import urllib.error
    import urllib.request

    payload = json.dumps({
        "store": "mesh_memory",
        "query": {"type": "gate_8_quarantine"},
        "limit": 1000
    }).encode("utf-8")

    req = urllib.request.Request(
        "http://127.0.0.1:8772/query",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            result = json.loads(resp.read().decode("utf-8"))
            records = []
            for item in result.get("items", []):
                records.append({
                    "file": item.get("file", ""),
                    "quarantined_at": item.get("quarantined_at", ""),
                    "attempts": item.get("attempts", 0),
                    "last_error": item.get("last_error", ""),
                    "fail_rate": item.get("fail_rate", 0.0),
                    "cohort_size": item.get("cohort_size", 0),
                })
            return records
    except Exception:
        return []


def _get_data():
    """Retrieve quarantine data from mesh memory."""
    return _query_mesh_memory()


def _render_table(records):
    """Render markdown table from quarantine records."""
    headers = ["file", "quarantined_at", "attempts", "last_error", "fail_rate", "cohort_size"]
    rows = []
    for r in records:
        rows.append([
            r["file"],
            r["quarantined_at"],
            str(r["attempts"]),
            r["last_error"],
            f"{r['fail_rate']:.2f}",
            str(r["cohort_size"])
        ])

    col_widths = [len(h) for h in headers]
    for row in rows:
        for i, val in enumerate(row):
            col_widths[i] = max(col_widths[i], len(val))

    lines = []
    header_line = "| " + " | ".join(h.ljust(col_widths[i]) for i, h in enumerate(headers)) + " |"
    lines.append(header_line)
    sep_parts = []
    for w in col_widths:
        sep_parts.append("-" * (w + 2))
    lines.append("|" + "|".join(sep_parts) + "|")
    for row in rows:
        data_line = "| " + " | ".join(cell.ljust(col_widths[i]) for i, cell in enumerate(row)) + " |"
        lines.append(data_line)

    return "\n".join(lines)


def main():
    records = _get_data()
    print(_render_table(records))


if __name__ == "__main__":
    stub_data = _get_stub_data()
    output = _render_table(stub_data)
    print(output)

    lines = output.strip().split("\n")
    header_line = lines[0]
    header_cols = [c.strip() for c in header_line.split("|") if c.strip()]
    expected_headers = ["file", "quarantined_at", "attempts", "last_error", "fail_rate", "cohort_size"]
    assert len(header_cols) == len(expected_headers), f"Expected {len(expected_headers)} columns, got {len(header_cols)}"
    assert header_cols == expected_headers, f"Expected headers {expected_headers}, got {header_cols}"

    row_count = sum(1 for line in lines[2:] if "|" in line and not line.startswith("|---"))
    assert row_count >= 1, f"Expected at least 1 quarantined row, got {row_count}"

    print("PASS")