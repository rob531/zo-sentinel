#!/usr/bin/env python3
"""
directory_presence_signal.py

Enrichment module scoring MCP presence in curated directory listings.
Reads directory_listings from metadata (e.g., Anthropic reference list).
Presence in gatekept directory is strongest single positive trust signal;
missing field contributes 0.
Phase II: Weighted scoring based on directory authority per Appendix C.
"""

from typing import Any


# Curated directory trust weights (Phase II - Appendix C)
DIRECTORY_TRUST_WEIGHTS = {
    "anthropic": 40.0,
    "openai": 35.0,
    "google": 25.0,
    "microsoft": 20.0,
    "meta": 20.0,
    "amazon": 15.0,
    "github": 10.0,
}

# Maximum score contribution per directory
MAX_SCORE_PER_DIRECTORY = 40.0


def compute_score(metadata: dict[str, Any]) -> tuple[float, dict[str, Any]]:
    """
    Compute trust score based on presence in curated directory listings.

    Args:
        metadata: Dict containing 'directory_listings' with curated directory info.
                  Each listing should be a dict with at least a 'name' field.

    Returns:
        Tuple of (score 0-100, evidence dict with scoring details)
    """
    evidence = {
        "signal_name": "directory_presence",
        "phase": "II",
        "directories_found": [],
        "directory_count": 0,
        "trust_breakdown": {},
        "scoring_logic": "weighted_presence_in_curated_directories",
    }

    # Missing field contributes 0
    if "directory_listings" not in metadata:
        evidence["status"] = "missing"
        evidence["score_reason"] = "No directory_listings field present"
        evidence["final_score"] = 0.0
        return 0.0, evidence

    listings = metadata.get("directory_listings", [])

    # Empty listings contribute 0
    if not listings:
        evidence["status"] = "empty"
        evidence["score_reason"] = "directory_listings field is empty"
        evidence["final_score"] = 0.0
        return 0.0, evidence

    total_score = 0.0

    # Process each directory listing
    for listing in listings:
        if not isinstance(listing, dict):
            continue

        dir_name = listing.get("name", "")
        if not dir_name:
            continue

        dir_name_lower = dir_name.lower()

        # Check if this is a curated directory
        if dir_name_lower in DIRECTORY_TRUST_WEIGHTS:
            weight = DIRECTORY_TRUST_WEIGHTS[dir_name_lower]
            
            # Verified listings get full weight, unverified get 50%
            is_verified = listing.get("verified", False)
            verification_multiplier = 1.0 if is_verified else 0.5
            
            score_contribution = min(weight * verification_multiplier, MAX_SCORE_PER_DIRECTORY)
            total_score += score_contribution

            evidence["directories_found"].append(dir_name)
            evidence["trust_breakdown"][dir_name] = {
                "base_weight": weight,
                "verified": is_verified,
                "multiplier": verification_multiplier,
                "score_contribution": score_contribution,
            }

    # Cap at 100
    final_score = min(100.0, total_score)

    evidence["directory_count"] = len(evidence["directories_found"])
    evidence["status"] = "found" if evidence["directory_count"] > 0 else "no_curated_matches"
    evidence["raw_score"] = total_score
    evidence["final_score"] = final_score
    evidence["score_reason"] = (
        f"MCP found in {evidence['directory_count']} curated directory(ies) "
        f"with trust score {final_score:.1f}/100"
    )

    return final_score, evidence


if __name__ == "__main__":
    print("=" * 60)
    print("Directory Presence Signal - Self-Test")
    print("=" * 60)

    # Test 1: Missing directory_listings field
    print("\n[TEST 1] Missing directory_listings field")
    metadata_empty = {"mcp_name": "test-mcp"}
    score1, evidence1 = compute_score(metadata_empty)
    assert 0 <= score1 <= 100, f"Score {score1} out of range [0,100]"
    assert score1 == 0.0, f"Expected score 0, got {score1}"
    print(f"  Score: {score1}")
    print(f"  Status: {evidence1['status']}")
    print("  PASS")

    # Test 2: Empty directory_listings
    print("\n[TEST 2] Empty directory_listings")
    metadata_none = {"mcp_name": "test-mcp", "directory_listings": None}
    score2, evidence2 = compute_score(metadata_none)
    assert 0 <= score2 <= 100, f"Score {score2} out of range [0,100]"
    print(f"  Score: {score2}")
    print("  PASS")

    # Test 3: Empty list
    print("\n[TEST 3] Empty directory list")
    metadata_empty_list = {"mcp_name": "test-mcp", "directory_listings": []}
    score3, evidence3 = compute_score(metadata_empty_list)
    assert 0 <= score3 <= 100, f"Score {score3} out of range [0,100]"
    assert score3 == 0.0, f"Expected score 0, got {score3}"
    print(f"  Score: {score3}")
    print("  PASS")

    # Test 4: Unknown directory only
    print("\n[TEST 4] Unknown directory only")
    metadata_unknown = {
        "mcp_name": "test-mcp",
        "directory_listings": [{"name": "random-unknown-dir"}],
    }
    score4, evidence4 = compute_score(metadata_unknown)
    assert 0 <= score4 <= 100, f"Score {score4} out of range [0,100]"
    assert score4 == 0.0, f"Expected score 0, got {score4}"
    print(f"  Score: {score4}")
    print("  PASS")

    # Test 5: Anthropic verified listing (highest trust)
    print("\n[TEST 5] Anthropic verified listing")
    metadata_anthropic = {
        "mcp_name": "test-mcp",
        "directory_listings": [
            {"name": "anthropic", "verified": True, "url": "https://..."}
        ],
    }
    score5, evidence5 = compute_score(metadata_anthropic)
    assert 0 <= score5 <= 100, f"Score {score5} out of range [0,100]"
    assert score5 > 0, f"Expected positive score, got {score5}"
    print(f"  Score: {score5}")
    print(f"  Directories: {evidence5['directories_found']}")
    print("  PASS")

    # Test 6: Multiple curated directories (verified)
    print("\n[TEST 6] Multiple curated directories (verified)")
    metadata_multi = {
        "mcp_name": "test-mcp",
        "directory_listings": [
            {"name": "anthropic", "verified": True},
            {"name": "openai", "verified": True},
            {"name": "google", "verified": True},
        ],
    }
    score6, evidence6 = compute_score(metadata_multi)
    assert 0 <= score6 <= 100, f"Score {score6} out of range [0,100]"
    assert len(evidence6["directories_found"]) == 3, "Should find 3 directories"
    print(f"  Score: {score6}")
    print(f"  Directories: {evidence6['directories_found']}")
    print(f"  Breakdown: {evidence6['trust_breakdown']}")
    print("  PASS")

    # Test 7: Multiple directories with mixed verification
    print("\n[TEST 7] Mixed verification status")
    metadata_mixed = {
        "mcp_name": "test-mcp",
        "directory_listings": [
            {"name": "anthropic", "verified": True},
            {"name": "openai", "verified": False},
            {"name": "microsoft", "verified": True},
        ],
    }
    score7, evidence7 = compute_score(metadata_mixed)
    assert 0 <= score7 <= 100, f"Score {score7} out of range [0,100]"
    print(f"  Score: {score7}")
    print(f"  Breakdown: {evidence7['trust_breakdown']}")
    print("  PASS")

    # Test 8: Case insensitivity
    print("\n[TEST 8] Case insensitivity")
    metadata_case = {
        "mcp_name": "test-mcp",
        "directory_listings": [
            {"name": "Anthropic", "verified": True},
            {"name": "OPENAI", "verified": True},
        ],
    }
    score8, evidence8 = compute_score(metadata_case)
    assert 0 <= score8 <= 100, f"Score {score8} out of range [0,100]"
    assert len(evidence8["directories_found"]) == 2, "Should find 2 directories"
    print(f"  Score: {score8}")
    print(f"  Directories: {evidence8['directories_found']}")
    print("  PASS")

    # Test 9: Score capping at 100
    print("\n[TEST 9] Score capping at 100")
    metadata_max = {
        "mcp_name": "test-mcp",
        "directory_listings": [
            {"name": "anthropic", "verified": True},
            {"name": "openai", "verified": True},
            {"name": "google", "verified": True},
            {"name": "microsoft", "verified": True},
            {"name": "meta", "verified": True},
            {"name": "amazon", "verified": True},
        ],
    }
    score9, evidence9 = compute_score(metadata_max)
    assert 0 <= score9 <= 100, f"Score {score9} out of range [0,100]"
    assert score9 == 100.0, f"Expected score 100, got {score9}"
    print(f"  Score: {score9} (capped)")
    print("  PASS")

    # Test 10: Phase II directive - detailed evidence structure
    print("\n[TEST 10] Phase II evidence structure validation")
    metadata_full = {
        "mcp_name": "full-test-mcp",
        "version": "1.0.0",
        "directory_listings": [
            {"name": "anthropic", "verified": True, "url": "https://example.com"},
            {"name": "github", "verified": True, "url": "https://github.com/example"},
        ],
    }
    score10, evidence10 = compute_score(metadata_full)
    assert 0 <= score10 <= 100, f"Score {score10} out of range [0,100]"
    assert "phase" in evidence10, "Missing phase field"
    assert evidence10["phase"] == "II", "Wrong phase"
    assert "trust_breakdown" in evidence10, "Missing trust_breakdown"
    assert "scoring_logic" in evidence10, "Missing scoring_logic"
    print(f"  Score: {score10}")
    print(f"  Phase: {evidence10['phase']}")
    print(f"  Scoring Logic: {evidence10['scoring_logic']}")
    print("  PASS")

    print("\n" + "=" * 60)
    print("ALL TESTS PASSED")
    print("=" * 60)
    print(f"\nFinal validation: All scores in range [0, 100]")
    print("Directory presence signal module ready for production.")