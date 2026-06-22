#!/usr/bin/env bash
# Seed goose Memory from the code graph with the SAME provider config the directive
# generator injects (the ladder shim on :8796) -- a bare `goose run` has "No provider
# configured". Self-locating, so it works in ANY checkout (zo_sentinel /
# zo_sentinel_pub_clone) and avoids the repo-local-vs-tower path fork. Idempotent;
# point a schedule at this. Mirrors sentinel_directive_generator_goose.py:330-333.
set -euo pipefail
export GOOSE_PROVIDER="${GOOSE_PROVIDER:-openai}"
export GOOSE_MODEL="${GOOSE_MODEL:-MiniMax-Text-01}"
export OPENAI_BASE_URL="${OPENAI_BASE_URL:-http://127.0.0.1:8796/v1}"
export OPENAI_API_KEY="${OPENAI_API_KEY:-dummy_key_for_shim}"
cd "$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"   # repo root of THIS clone
exec goose run --recipe goose_recipes/seed_graph_memory.yaml
