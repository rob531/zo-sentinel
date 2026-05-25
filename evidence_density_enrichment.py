"""
Evidence Density Enrichment

Scores MCP servers by EVIDENCE DENSITY -- the breadth of distinct fact-types
recorded in mcp_registry_facts for the server. This signal is critical
because the verdict-uniformity diagnostic shows 5/6 existing signals have
1 distinct value across 3786 servers, while only 45 servers currently have
any facts at all. Discriminating on evidence density immediately produces
a non-flat distribution.

CONTRACT (per SENTINEL_DIRECTIVE_SCHEMA.md, signal-enrichment shape):
  - Pure function: compute_score(metadata: dict) -> tuple[float, dict]
  - Same input -> same output. No side effects.
  - No DB writes. No network calls. No file I/O. No project imports.
  - Returns (score in [0.0, 100.0], evidence dict).

INTEGRATION:
  Bridged into mcp_signal_scores by signal_bridge.py per existing convention.
  Run by enrichment_harness.py against synthetic inputs before promotion.

=============================================================================
REPLACEMENT NOTE -- 2026-04-28 15:35Z (CTO audit)
=============================================================================
The original LLM-generated v1 of this file built a full daemon with two
eval() calls on HTTP response bodies (RCE vector) and used
  str(payload).encode('utf-8')
to POST to write_service, which serializes Python repr instead of JSON.
Every write would silently 500 and the bare-except would swallow it,
producing a daemon that runs forever heartbeating without scoring anything.

The smoke test passed because it only checks import/exec validity, not
contract conformance or basic security hygiene. Filed for follow-up:
harden the smoke gate to flag eval() and to lint write_service callers
for json= kwarg usage.

This replacement keeps the compute_score logic from v1 (which was correct)
and drops everything else. Pure function only, exactly as the directive
requested.
"""
from typing import Dict, Tuple, Any

SIGNAL_NAME = "evidence_density"
VERSION = "1.1.0"  # 1.0 was the unsafe daemon-shaped output; 1.1 is the contract-conformant rewrite
MAX_SCORE = 100.0
EXPECTED_FACT_TYPES = 6

FACT_TYPE_KEYS = [
    "fact_count_npm",
    "fact_count_github",
    "fact_count_ecosystems",
    "fact_count_registry",
    "fact_count_threat",
    "fact_count_signal",
]


def compute_score(metadata: Dict[str, Any]) -> Tuple[float, Dict[str, Any]]:
    """Score a server by the breadth of evidence collected about it.

    Reads six fact-type counts from metadata; returns a score proportional to
    how many fact types are non-zero, plus a small bonus for raw fact volume.

    Args:
        metadata: server metadata dict. Recognised keys: fact_count_npm,
            fact_count_github, fact_count_ecosystems, fact_count_registry,
            fact_count_threat, fact_count_signal. Missing keys default to 0.

    Returns:
        (score, evidence) where score is clamped to [0.0, 100.0] and evidence
        is a dict listing per-fact-type contributions and the rationale.
    """
    counts = {k: int(metadata.get(k, 0) or 0) for k in FACT_TYPE_KEYS}
    types_present = sum(1 for v in counts.values() if v > 0)
    total_facts = sum(counts.values())

    # Base score: proportional to fact-type breadth (0..90).
    breadth_score = (types_present / EXPECTED_FACT_TYPES) * 90.0

    # Volume bonus: small reward for raw count beyond presence (0..10).
    # Caps at 10 so a server with 50 facts of one type can't outrank a
    # server with broad coverage. log-style scaling via min().
    volume_bonus = min(10.0, total_facts / 5.0)

    score = breadth_score + volume_bonus
    score = max(0.0, min(MAX_SCORE, score))

    evidence: Dict[str, Any] = {
        "signal_name": SIGNAL_NAME,
        "version": VERSION,
        "types_present": types_present,
        "types_total": EXPECTED_FACT_TYPES,
        "total_facts": total_facts,
        "breadth_score": round(breadth_score, 2),
        "volume_bonus": round(volume_bonus, 2),
        "per_fact_type": counts,
        "final_score": round(score, 1),
    }
    return round(score, 1), evidence


if __name__ == "__main__":
    test_cases = [
        {
            "name": "No evidence at all",
            "metadata": {},
        },
        {
            "name": "Only one fact type",
            "metadata": {"fact_count_npm": 1},
        },
        {
            "name": "Two fact types, low volume",
            "metadata": {"fact_count_npm": 2, "fact_count_github": 1},
        },
        {
            "name": "Three fact types, modest volume",
            "metadata": {
                "fact_count_npm": 4,
                "fact_count_github": 3,
                "fact_count_ecosystems": 2,
            },
        },
        {
            "name": "Full breadth, low volume",
            "metadata": {k: 1 for k in FACT_TYPE_KEYS},
        },
        {
            "name": "Full breadth, high volume",
            "metadata": {k: 10 for k in FACT_TYPE_KEYS},
        },
        {
            "name": "Lopsided: 50 of one type, nothing else",
            "metadata": {"fact_count_npm": 50},
        },
    ]
    print(f"Evidence Density Enrichment v{VERSION}")
    print("=" * 60)
    for tc in test_cases:
        score, ev = compute_score(tc["metadata"])
        print(f"\nTest: {tc['name']}")
        print(f"  Score: {score}")
        print(f"  Types present: {ev['types_present']}/{ev['types_total']}")
        print(f"  Total facts: {ev['total_facts']}")
        print(f"  Breadth score: {ev['breadth_score']}, Volume bonus: {ev['volume_bonus']}")