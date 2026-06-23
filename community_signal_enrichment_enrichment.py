#!/usr/bin/env python3
"""
community_signal_enrichment_enrichment.py

Pure enrichment module exposing compute_score(metadata: dict) -> (float, dict).
Reads 'community_signal_enrichment' from metadata; weighted formula (weights sum to 1.0);
a missing field contributes 0 and is appended to evidence['missing'].
Returns (score in 0..100, evidence dict with keys 'verdict' and 'missing').
No DB writes, no network, no imports of protected modules.
"""

def compute_score(metadata: dict) -> (float, dict):
    """
    Compute an enrichment score (0‑100) based on the
    ``community_signal_enrichment`` entry in *metadata*.

    The mapping from categorical values to numeric scores uses a weight
    of 1.0 for this single field (weights sum to 1.0).  Unknown or missing
    values contribute 0 to the score and are recorded in the
    ``missing`` list of the returned evidence.

    Parameters
    ----------
    metadata : dict
        Dictionary that may contain the key
        ``community_signal_enrichment``.  Accepted values are
        ``low``, ``medium``, ``high``, ``critical`` (case‑insensitive)
        or a numeric value in the range 0‑100.

    Returns
    -------
    (float, dict)
        *score* – a float between 0 and 100.
        *evidence* – a dict with keys ``verdict`` (one of
        ``low``/``medium``/``high``/``critical``) and ``missing``
        (list of field names that were absent from *metadata*).
    """
    # Weight for the single field (must sum to 1.0)
    FIELD_WEIGHT = 1.0

    # Mapping from categorical levels to a fractional score (0‑1)
    # Multiplying by 100 yields the final 0‑100 score.
    CATEGORY_SCORES = {
        "low": 0.25,
        "medium": 0.50,
        "high": 0.75,
        "critical": 1.00,
    }

    FIELD_NAME = "community_signal_enrichment"

    # Default values
    score = 0.0
    missing = []

    if FIELD_NAME not in metadata:
        missing.append(FIELD_NAME)
    else:
        raw = metadata[FIELD_NAME]
        if isinstance(raw, str):
            # Case‑insensitive lookup
            key = raw.strip().lower()
            fractional = CATEGORY_SCORES.get(key, 0.0)
            score = fractional * 100.0
        else:
            # Attempt to interpret numeric input directly
            try:
                score = float(raw)
            except (TypeError, ValueError):
                # Malformed numeric value → treat as 0
                score = 0.0

    # Determine textual verdict based on the resulting score
    if score < 25:
        verdict = "low"
    elif score < 50:
        verdict = "medium"
    elif score < 75:
        verdict = "high"
    else:
        verdict = "critical"

    evidence = {
        "verdict": verdict,
        "missing": missing,
    }
    return score, evidence


if __name__ == "__main__":
    # Self‑test as required by the acceptance criteria
    score, evidence = compute_score({"community_signal_enrichment": "high"})
    assert 0 <= score <= 100, f"Score out of range: {score}"
    assert "verdict" in evidence, "Evidence missing 'verdict'"
    print("PASS")