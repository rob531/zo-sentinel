def minimax_generate(prompt: str, model: str = "MiniMax-M1") -> str:
    """
    MiniMax native chat completions API.
    Endpoint: https://api.minimax.io/v1/text/chatcompletion_v2
    The Anthropic-compat endpoint requires a higher plan tier (HTTP 500 on basic).
    Native endpoint works on all plans.
    """
    api_key = os.environ.get("MINIMAX_API_KEY", "")
    if not api_key:
        log.warning("  minimax: MINIMAX_API_KEY not set")
        return ""
    try:
        r = requests.post(
            "https://api.minimax.io/v1/text/chatcompletion_v2",
            headers={"Authorization": f"Bearer {api_key}",
                     "Content-Type": "application/json"},
            json={"model": model,
                  "messages": [{"role": "user", "content": prompt}],
                  "max_tokens": 4096},
            timeout=120
        )
        if r.status_code == 200:
            choices = r.json().get("choices", [])
            text = choices[0].get("message", {}).get("content", "").strip() if choices else ""
            valid, reason = content_is_valid(text)
            if valid:
                log.info(f"  minimax: ({len(text)}b)")
                return text
            log.warning(f"  minimax: {reason}")
        else:
            log.warning(f"  minimax: HTTP {r.status_code} {r.text[:100]}")
    except Exception as e:
        log.warning(f"  minimax: {e}")
    return ""