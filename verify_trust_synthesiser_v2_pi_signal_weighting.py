import trust_synthesiser_v2

def verify_injection_resilience_weighting():
    # Test cases with varying injection_resilience scores
    test_cases = [
        # (mcp_signal_scores, expected_trust_score)
        ({'injection_resilience': 0.0}, 0.0),  # Below threshold
        ({'injection_resilience': 0.79}, 0.0),  # Below threshold
        ({'injection_resilience': 0.80}, 0.80 * 1.6),  # At threshold
        ({'injection_resilience': 0.90}, 0.90 * 1.6),  # Above threshold
        ({'injection_resilience': 1.0}, 1.0 * 1.6),  # Max score
    ]

    for mcp_signal_scores, expected_trust_score in test_cases:
        # Calculate trust score
        trust_score = trust_synthesiser_v2.calculate_trust_score(mcp_signal_scores)

        # Verify the injection_resilience weighting
        assert trust_score == expected_trust_score, \
            f"Failed for {mcp_signal_scores}. Expected {expected_trust_score}, got {trust_score}"

    print("Verification PASS")

if __name__ == "__main__":
    verify_injection_resilience_weighting()