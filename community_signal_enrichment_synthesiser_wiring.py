import os

# Read the enrichment integration to understand its output schema
if os.path.exists('/home/workspace/zo_sentinel/community_signal_enrichment_integration.py'):
    with open('/home/workspace/zo_sentinel/community_signal_enrichment_integration.py') as f:
        print("=== enrichment_integration ===")
        print(f.read())

# Read the synthesiser to understand how to wire
if os.path.exists('/home/workspace/zo_sentinel/trust_synthesiser_v2.py'):
    with open('/home/workspace/zo_sentinel/trust_synthesiser_v2.py') as f:
        print("=== synthesiser_v2 ===")
        print(f.read())

# Check for the failed output file
if os.path.exists('/home/workspace/zo_sentinel/community_signal_enrichment_synthesiser_wiring.py'):
    with open('/home/workspace/zo_sentinel/community_signal_enrichment_synthesiser_wiring.py') as f:
        print("=== failed wiring file ===")
        print(f.read())