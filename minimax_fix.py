def minimax_generate(prompt: str, model: str = "MiniMax-M2.7") -> str:
    """
    MiniMax via OpenAI-compatible endpoint.
    Token Plan keys (sk-cp-) use /v1/chat/completions, NOT /v1/text/chatcompletion_v2.
    Model: MiniMax-M2.7 (not MiniMax-M1, not abab6.5s-chat).
    """
    api_key = os.environ.get("MINIMAX_API_KEY", "")
    if not api_key:
        log.warning("  minimax: MINIMAX_API_KEY not set")
        return ""
    try:
        r = requests.post(
            "https://api.minimax.io/v1/chat/completions",
            headers={"Authorization": "Bearer " + api_key,
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
                log.info("  minimax: (%db)", len(text))
                return text
            log.warning("  minimax: %s", reason)
        else:
            body = r.json() if r.headers.get("content-type","").startswith("application/json") else r.text
            log.warning("  minimax: HTTP %s %s", r.status_code, str(body)[:120])
    except Exception as e:
        log.warning("  minimax: %s", e)
    return ""