"""
Trust Gating Service - logic.py
Evaluates URL/name trust based on curated allowlist/blocklist and axis scores.
"""

from fastapi import APIRouter, Query, HTTPException
from pydantic import BaseModel
from datetime import datetime, timezone
from typing import Optional

router = APIRouter(prefix="/api", tags=["trust_gating"])


class TrustGateResponse(BaseModel):
    """Response model for trust gate endpoint"""
    url: str
    name: str
    verdict: str | None
    risk_tier: str | None
    matched_rule: str | None
    checked_at: str


# Curated allowlist of known-good domains
_curated_allowlist: dict[str, dict] = {
    "google.com": {"verdict": "trusted", "tier": "low", "rule": "allowlist_google"},
    "github.com": {"verdict": "trusted", "tier": "low", "rule": "allowlist_github"},
    "microsoft.com": {"verdict": "trusted", "tier": "low", "rule": "allowlist_microsoft"},
    "openai.com": {"verdict": "trusted", "tier": "low", "rule": "allowlist_openai"},
    "anthropic.com": {"verdict": "trusted", "tier": "low", "rule": "allowlist_anthropic"},
}

# Curated blocklist of known-bad domains
_curated_blocklist: dict[str, dict] = {
    "evil.com": {"verdict": "blocked", "tier": "high", "rule": "blocklist_evil"},
    "malware.ru": {"verdict": "blocked", "tier": "high", "rule": "blocklist_malware"},
    "phishing.net": {"verdict": "blocked", "tier": "high", "rule": "blocklist_phishing"},
    "suspicious.org": {"verdict": "blocked", "tier": "high", "rule": "blocklist_suspicious"},
}


def trust_gate(url: str, name: str, axis_scores: dict | None = None) -> dict:
    """
    Evaluate trust for a given URL and name.
    
    Checks curated allowlist/blocklist first, then looks for overrides,
    and finally delegates to axis scores if provided.
    
    Args:
        url: The URL to evaluate
        name: The name/entity to evaluate  
        axis_scores: Optional dict of axis scores for fallback evaluation
        
    Returns:
        dict with keys: verdict (str), tier (str), overrides (list), source (str)
    """
    url_lower = url.lower()
    name_lower = name.lower()
    
    # Check curated allowlist first
    if url_lower in _curated_allowlist:
        entry = _curated_allowlist[url_lower]
        return {
            "verdict": entry["verdict"],
            "tier": entry["tier"],
            "overrides": [],
            "source": "curated_allowlist"
        }
    
    # Check curated blocklist
    if url_lower in _curated_blocklist:
        entry = _curated_blocklist[url_lower]
        return {
            "verdict": entry["verdict"],
            "tier": entry["tier"],
            "overrides": [],
            "source": "curated_blocklist"
        }
    
    # Try to load trust_gating_override.py from app/ or shared/
    override_result = _load_override(url_lower, name_lower)
    if override_result:
        return override_result
    
    # Delegate to axis scores as final fallback
    return _evaluate_from_axis_scores(axis_scores)


def _load_override(url_lower: str, name_lower: str) -> dict | None:
    """Load and check trust_gating_override.py if it exists"""
    try:
        import importlib.util
        
        # Try app.trust_gating_override first, then shared.trust_gating_override
        for module_name in ["app.trust_gating_override", "shared.trust_gating_override"]:
            spec = importlib.util.find_spec(module_name)
            if spec and spec.loader:
                mod = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(mod)
                overrides = getattr(mod, 'OVERRIDES', {})
                
                # Check URL-based override
                if url_lower in overrides:
                    ov = overrides[url_lower]
                    return {
                        "verdict": ov.get("verdict", "blocked"),
                        "tier": ov.get("tier", "high"),
                        "overrides": [url_lower],
                        "source": "override_db"
                    }
                
                # Check name-based override
                if name_lower in overrides:
                    ov = overrides[name_lower]
                    return {
                        "verdict": ov.get("verdict", "blocked"),
                        "tier": ov.get("tier", "high"),
                        "overrides": [name_lower],
                        "source": "override_db"
                    }
    except (ImportError, ModuleNotFoundError, AttributeError, Exception):
        pass
    
    return None


def _evaluate_from_axis_scores(axis_scores: dict | None) -> dict:
    """Evaluate trust tier from axis scores when no curated match exists"""
    tier = "high"  # Default to high risk for unknown
    
    if axis_scores:
        try:
            # Compute composite score from axis scores
            values = [v for v in axis_scores.values() if isinstance(v, (int, float))]
            if values:
                composite = sum(values) / len(values)
                if composite >= 0.7:
                    tier = "low"
                elif composite >= 0.4:
                    tier = "medium"
                else:
                    tier = "high"
        except (TypeError, ZeroDivisionError):
            tier = "high"
    
    return {
        "verdict": "unknown",
        "tier": tier,
        "overrides": [],
        "source": "axis_scores"
    }


@router.get("/trust/gate", response_model=TrustGateResponse)
async def endpoint(
    url: str = Query(..., description="URL to evaluate"),
    name: str = Query(..., description="Name/entity to evaluate"),
    axis_scores: str | None = Query(None, description="JSON string of axis scores")
) -> TrustGateResponse:
    """
    GET /api/trust/gate endpoint
    
    Evaluates trust for a URL/name combination based on:
    1. Curated allowlist (known-good domains)
    2. Curated blocklist (known-bad domains)
    3. trust_gating_override.py overrides
    4. Axis scores for risk tier calculation
    """
    import json
    
    parsed_scores = None
    if axis_scores:
        try:
            parsed_scores = json.loads(axis_scores)
        except json.JSONDecodeError:
            pass
    
    result = trust_gate(url, name, parsed_scores)
    
    matched_rule = None
    if result["source"] == "curated_allowlist":
        matched_rule = f"allowlist:{url}"
    elif result["source"] == "curated_blocklist":
        matched_rule = f"blocklist:{url}"
    elif result["source"] == "override_db" and result["overrides"]:
        matched_rule = f"override:{result['overrides'][0]}"
    
    return TrustGateResponse(
        url=url,
        name=name,
        verdict=result["verdict"],
        risk_tier=result["tier"],
        matched_rule=matched_rule,
        checked_at=datetime.now(timezone.utc).isoformat()
    )


if __name__ == "__main__":
    # Self-test with 3 test cases: 1 trusted, 1 blocked, 1 unknown
    print("Running trust_gating self-test...")
    
    # Test case 1: Trusted domain from allowlist
    result1 = trust_gate("google.com", "google-corp", {"trust_score": 0.9, "safety_score": 0.95})
    assert result1["verdict"] == "trusted", f"Expected trusted, got {result1['verdict']}"
    assert result1["tier"] == "low", f"Expected low tier, got {result1['tier']}"
    
    # Test case 2: Blocked domain from blocklist
    result2 = trust_gate("evil.com", "evil-corp", {"trust_score": 0.1, "safety_score": 0.05})
    assert result2["verdict"] == "blocked", f"Expected blocked, got {result2['verdict']}"
    assert result2["tier"] == "high", f"Expected high tier, got {result2['tier']}"
    
    # Test case 3: Unknown domain - delegates to axis scores
    result3 = trust_gate("unknown.example", "unknown-entity", {"trust_score": 0.6, "safety_score": 0.5})
    assert result3["verdict"] == "unknown", f"Expected unknown, got {result3['verdict']}"
    assert result3["source"] == "axis_scores", f"Expected axis_scores source, got {result3['source']}"
    
    print("All assertions passed!")
    print("PASS")