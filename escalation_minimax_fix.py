def _call_minimax_direct(spec, prompt, system, max_tokens, temperature):
    """MiniMax direct via api.minimax.io/v1/chat/completions.

    v0.4: removed reasoning_split=True.
    reasoning_split is needed in the builder (large code prompts) but causes
    empty content on short prompts in the escalation ladder -- MiniMax puts
    everything into reasoning_details and returns content='' which triggers
    a spurious 'empty content' failure. General inference doesn't need it.
    Inline <think> tags are stripped manually as belt-and-suspenders.
    """
    import re
    import requests
    key = os.environ.get("MINIMAX_API_KEY")
    if not key:
        return None, "MINIMAX_API_KEY not set"
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})
    try:
        r = requests.post(
            "https://api.minimax.io/v1/chat/completions",
            headers={"Authorization": f"Bearer {key}",
                     "Content-Type": "application/json"},
            json={"model": spec.model_id,
                  "messages": messages,
                  "max_tokens": max_tokens,
                  "temperature": temperature},
            timeout=240,
        )
        r.raise_for_status()
        choices = r.json().get("choices", [])
        if not choices:
            return None, "empty choices"
        raw = choices[0].get("message", {}).get("content", "").strip()
        # Strip inline reasoning tags as belt-and-suspenders
        content = re.sub(r"<think>.*?</think>", "", raw, flags=re.DOTALL).strip()
        content = content if content else raw
        return (content or None), (None if content else "empty content")
    except Exception as e:
        return None, f"{type(e).__name__}: {e}"