# MiniMax endpoint — native chat/completions API
# The Anthropic-compat endpoint (api.minimax.io/anthropic/v1/messages)
# requires a higher plan tier and returns HTTP 500 on basic plans.
# Use the native endpoint instead:
MINIMAX_API_URL = "https://api.minimax.io/v1/text/chatcompletion_v2"
# Model: MiniMax-M1 (128k context, strong at code)
# Auth: Authorization: Bearer <MINIMAX_API_KEY>
# Body: {"model": "MiniMax-M1", "messages": [{"role": "user", "content": "..."}]}
# Response: {"choices": [{"message": {"content": "..."}}]}