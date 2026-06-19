def compute_score(metadata: dict) -> tuple[float, dict]:
    """
    Enriches the known_bad_pattern signal by evaluating multiple metadata features.
    Returns a score [0.0, 100.0] and a breakdown dictionary.
    """
    # Default values for missing metadata
    registry_source = metadata.get("registry_source", "unknown")
    age_days = float(metadata.get("age_days", 365))
    download_count = float(metadata.get("download_count", 0))
    dependency_count = float(metadata.get("dependency_count", 0))
    publisher_verified = bool(metadata.get("publisher_verified", False))
    stars = float(metadata.get("stars", 0))
    tool_count = float(metadata.get("tool_count", 0))

    # Risk factors (Higher = more suspicious)
    # 1. Age: Newer packages are riskier
    age_risk = max(0.0, min(100.0, (100.0 - (age_days / 3.65))))
    
    # 2. Popularity: Low stars/downloads relative to dependencies is suspicious
    popularity_risk = 0.0
    if download_count < 100 and stars < 5:
        popularity_risk = 40.0
    
    # 3. Trust: Unverified publishers are riskier
    trust_risk = 0.0 if publisher_verified else 30.0
    
    # 4. Complexity: High dependency/tool count with low popularity is a red flag
    complexity_risk = min(30.0, (dependency_count * 0.5) + (tool_count * 2.0))
    
    # 5. Source: Certain registries are historically higher risk
    source_risk = 20.0 if registry_source in ["unknown", "unofficial"] else 0.0

    # Weighted calculation
    raw_score = (
        (age_risk * 0.3) + 
        (popularity_risk * 0.25) + 
        (trust_risk * 0.2) + 
        (complexity_risk * 0.15) + 
        (source_risk * 0.1)
    )

    # Ensure score is within [0.0, 100.0] and provides high granularity
    final_score = round(max(0.0, min(100.0, raw_score)), 2)
    
    breakdown = {
        "age_risk": round(age_risk, 2),
        "popularity_risk": round(popularity_risk, 2),
        "trust_risk": round(trust_risk, 2),
        "complexity_risk": round(complexity_risk, 2),
        "source_risk": round(source_risk, 2)
    }

    return final_score, breakdown