def compute_score(metadata: dict) -> tuple[float, dict]:
    """
    Computes a domain age score based on multiple metadata fields.
    Score range: [0, 100]
    Evidence dict lists partial scores per field.
    """
    evidence = {}
    total_score = 0.0

    # Validate required fields
    required_fields = ['age_days', 'registry_source', 'publisher_verified']
    missing_fields = [field for field in required_fields if field not in metadata]
    if missing_fields:
        raise ValueError(f"Missing required fields: {missing_fields}")

    # Base score from age_days (0-100)
    age_days = metadata['age_days']
    age_score = min(100, age_days / 365 * 100)  # Normalize to 0-100 scale
    evidence['age_days'] = age_score
    total_score += age_score

    # Adjust based on registry_source (0-20)
    registry_source = metadata['registry_source'].lower()
    registry_scores = {
        'icann': 20,
        'cc': 15,
        'other': 10,
        'unknown': 5
    }
    registry_score = registry_scores.get(registry_source, 5)
    evidence['registry_source'] = registry_score
    total_score += registry_score

    # Adjust based on publisher_verified (0-10)
    publisher_verified = metadata['publisher_verified']
    verified_score = 10 if publisher_verified else 0
    evidence['publisher_verified'] = verified_score
    total_score += verified_score

    # Bonus for domain_trust_score if available (0-10)
    if 'domain_trust_score' in metadata:
        trust_score = metadata['domain_trust_score']
        trust_bonus = min(10, trust_score / 10)  # Normalize to 0-10 scale
        evidence['domain_trust_score'] = trust_bonus
        total_score += trust_bonus

    # Normalize total score to 0-100 range
    total_score = min(100, max(0, total_score))

    # Add discrimination by applying non-linear scaling
    if total_score > 80:
        total_score = 80 + (total_score - 80) * 1.5  # Expand high scores
    elif total_score < 20:
        total_score = 20 - (20 - total_score) * 0.5  # Compress low scores

    total_score = min(100, max(0, total_score))

    return (round(total_score, 2), evidence)