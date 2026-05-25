import os
import sys

# Read current attestation_engine.py
attestation_path = '/home/workspace/zo_sentinel/attestation_engine.py'
with open(attestation_path, 'r') as f:
    attestation_content = f.read()

print("=== Current attestation_engine.py content ===")
print(attestation_content)
print("\n=== Checking for Phase 8 evidence references ===")

# Check for key Phase 8 terms
phase8_terms = ['injection_resilience', 'pi_scorer', 'pi_results', 'corpus_hash', 'pi_quara', 'eighth_signal', '8th']
found_terms = []
for term in phase8_terms:
    if term in attestation_content:
        found_terms.append(term)

print(f"Phase 8 terms found in attestation_engine.py: {found_terms if found_terms else 'NONE'}")

# The task is clear - attestation_engine.py does NOT include Phase 8 dynamic evidence
# Generate attestation_engine_v2.py that extends to include injection_resilience and pi_results
print("\n=== Generating attestation_engine_v2.py with Phase 8 dynamic evidence ===")
sys.exit(0)