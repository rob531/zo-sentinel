# services/staged/risk_tier_axis_aggregator/logic.py
import requests  # noqa: F401  (dependency declared in spec)

from fastapi import Depends
from app.db import get_session
from app.models import McpLlmAxisScore, McpServerRegistry

# The seven axes that contribute to the composite risk tier
_AXES = [
    "overall_risk",
    "auth_strength",
    "capability_breadth",
    "data_sensitivity",
    "network_egress",
    "maintainer_trust",
    "exploit_surface",
]

# --------------------------------------------------------------------------- #
# Pure computation ----------------------------------------------------------- #
# --------------------------------------------------------------------------- #
def compute_risk_tier(
    server_id: str, axis_rows: list[dict]
) -> tuple[str, float, dict]:
    """
    Compute a composite risk tier for a server.

    Parameters
    ----------
    server_id: str
        Identifier of the server (used only for reporting).
    axis_rows: list[dict]
        Each dict must contain at least ``axis_name`` and ``p_top`` keys,
        representing a row from ``mcp_llm_axis_scores`` for the server.

    Returns
    -------
    tuple
        (tier_name, composite_score, details_dict)

        * tier_name – one of the tier strings defined in the spec.
        * composite_score – the weighted (here, simple average) score.
        * details_dict – mapping of axis → p_top and a list of missing axes.
    """
    # Build a mapping axis_name → p_top
    axis_map: dict[str, float] = {
        row["axis_name"]: float(row["p_top"]) for row in axis_rows
    }

    # Determine which axes are missing
    missing_axes = [a for a in _AXES if a not in axis_map]

    # Insufficient data rule (≥5 missing axes)
    if len(missing_axes) >= 5:
        return (
            "INSUFFICIENT",
            0.0,
            {"missing_axes": missing_axes, "axis_p_top": axis_map},
        )

    # Composite score – simple average across the seven axes
    composite = sum(axis_map[a] for a in _AXES if a in axis_map) / len(_AXES)

    # Tier selection based on composite score thresholds
    if composite > 75:
        tier = "TRUSTED_GENERAL"
    elif composite > 60:
        tier = "TRUSTED_RESEARCH"
    elif composite > 45:
        tier = "ENTERPRISE_CONTROLLED"
    elif composite > 30:
        tier = "CAUTION_LIMITED"
    elif composite > 15:
        tier = "HIGH_RISK_ISOLATED"
    else:
        tier = "KNOWN_THREAT"

    details = {
        "axis_p_top": {a: axis_map.get(a) for a in _AXES},
        "missing_axes": missing_axes,
    }
    return tier, composite, details


# --------------------------------------------------------------------------- #
# DB‑backed helper ----------------------------------------------------------- #
# --------------------------------------------------------------------------- #
def get_risk_tier_for_server(
    server_id: str, session=Depends(get_session)
) -> tuple[str, float, dict]:
    """
    Retrieve all LLM axis scores for a server from the DB and compute its risk tier.
    """
    rows = (
        session.query(McpLlmAxisScore)
        .filter(McpLlmAxisScore.server_id == server_id)
        .all()
    )
    axis_rows = [{"axis_name": r.axis_name, "p_top": r.p_top} for r in rows]
    return compute_risk_tier(server_id, axis_rows)


# --------------------------------------------------------------------------- #
# Self‑test ----------------------------------------------------------------- #
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    # Synthetic data for three representative servers
    def make_rows(p_top: float) -> list[dict]:
        return [{"axis_name": axis, "p_top": p_top} for axis in _AXES]

    # 1. TRUSTED_GENERAL (>75)
    tg_rows = make_rows(80.0)
    tier, score, _ = compute_risk_tier("srv_trusted_general", tg_rows)
    assert tier == "TRUSTED_GENERAL"
    assert abs(score - 80.0) < 1e-2

    # 2. ENTERPRISE_CONTROLLED (>45, ≤60)
    ec_rows = make_rows(50.0)
    tier, score, _ = compute_risk_tier("srv_enterprise_controlled", ec_rows)
    assert tier == "ENTERPRISE_CONTROLLED"
    assert abs(score - 50.0) < 1e-2

    # 3. HIGH_RISK_ISOLATED (>15, ≤30)
    hr_rows = make_rows(20.0)
    tier, score, _ = compute_risk_tier("srv_high_risk", hr_rows)
    assert tier == "HIGH_RISK_ISOLATED"
    assert abs(score - 20.0) < 1e-2

    print("PASS")