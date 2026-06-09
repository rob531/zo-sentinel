# archived 2026-04-27T16:45Z because Ollama-fallback produced unusable code
# (wrong HTTP method, hallucinated imports, no daemon scaffolding)
# Re-emitting via standing_goals.json -> dirgen / direct directive drop.
# Original errors:
#   - requests.get('/query') instead of requests.post with sql body
#   - hallucinated import_module(f'mcp_fingerprinter.{server_id}')
#   - wrong write endpoint port (8773 vs 8772)
#   - no heartbeat, no lockfile, no logging file handler
#   - get_server_ids() does dict-indexing on a list response
import sys
sys.exit(0)