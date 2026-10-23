from typing import Dict

# Import the session getter and models to satisfy the data‑access contract.
# They are not used in the pure function but must be available.
from app.db import get_session  # noqa: F401
from app.models import McpLlmAxisScore, McpServerRegistry  # noqa: F401


def compute_tier(axes: Dict[str, str]) -> str:
    """
    Compute an overall risk tier from individual axis labels.

    The function is pure: it only inspects the ``axes`` mapping and returns a
    string representing the highest severity observed.

    Parameters
    ----------
    axes: Dict[str, str]
        Mapping from axis name (e.g., ``"trust"``) to its risk label
        (e.g., ``"low"``, ``"top"``, ``"critical"``).

    Returns
    -------
    str
        The overall risk tier. One of:
        ``"critical"``, ``"danger"``, ``"top"``, ``"high"``,
        ``"medium"``, ``"low"``.
    """
    # Define severity ordering – higher index means more severe.
    severity_order = [
        "low",
        "medium",
        "high",
        "top",
        "danger",
        "critical",
    ]

    # Normalise labels to lower‑case strings.
    normalised = [label.lower() for label in axes.values() if isinstance(label, str)]

    # Determine the most severe label present.
    most_severe = "low"
    for label in normalised:
        if label in severity_order and severity_order.index(label) > severity_order.index(
            most_severe
        ):
            most_severe = label

    return most_severe


if __name__ == "__main__":
    # Simple self‑test without touching a real database.
    # Two example servers are described via plain dictionaries.
    server_axes = {
        "test_server_1": {
            "trust": "low",
            "confidentiality": "low",
            "integrity": "low",
            "availability": "low",
            "reliability": "low",
            "performance": "low",
            "compliance": "low",
        },
        "test_server_2": {
            "trust": "critical",
            "confidentiality": "high",
            "integrity": "medium",
            "availability": "low",
            "reliability": "low",
            "performance": "low",
            "compliance": "low",
        },
    }

    # Verify that the tier for the first server is ``low``.
    tier = compute_tier(server_axes["test_server_1"])
    assert tier == "low", f"expected 'low', got {tier!r}"

    # Verify that the tier for the second server is ``critical``.
    tier = compute_tier(server_axes["test_server_2"])
    assert tier == "critical", f"expected 'critical', got {tier!r}"

    print("PASS")