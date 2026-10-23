#!/usr/bin/env python3
"""build_via_rung.py -- ask ONE ladder rung to build a single self-contained HTML
app from a directive, via that provider's OpenAI-compatible endpoint. Self-sources
keys from /root/.zo_secrets (browser UA, like the shim). The bake-off build step:
same directive -> every rung -> an .html the treewalk then scores.

RUNGS mirrors escalation.py. Gemini uses its OpenAI-compat endpoint; Anthropic uses
its messages API. Override a model via <RUNG>_BUILD_MODEL env.
"""
import json, os, re, time, urllib.request, urllib.error

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")

# rung -> (base_url, key_env, secret_token, default_model, extra_params, kind)
RUNGS = {
    # --- new free capacity rungs ---
    "cerebras": ("https://api.cerebras.ai/v1", "CEREBRAS_API_KEY", "cereb", "gpt-oss-120b", {"reasoning_effort": "medium"}, "oai"),
    "groq":     ("https://api.groq.com/openai/v1", "GROQ_API_KEY", "groq", "openai/gpt-oss-120b", {}, "oai"),
    "mistral":  ("https://api.mistral.ai/v1", "MISTRAL_API_KEY", "mistral", "codestral-latest", {}, "oai"),
    "nvidia":   ("https://integrate.api.nvidia.com/v1", "NVIDIA_API_KEY", "nvidia", "mistralai/mistral-nemotron", {}, "oai"),
    # --- older / incumbent rungs ---
    "gemini":   ("https://generativelanguage.googleapis.com/v1beta/openai", "GEMINI_API_KEY", "gemini", "gemini-2.5-flash", {}, "oai"),
    "anthropic":("https://api.anthropic.com", "ANTHROPIC_API_KEY", "anthropic", "claude-haiku-4-5-20251001", {}, "anthropic"),
    "minimax":  ("https://api.minimax.io/v1", "MINIMAX_API_KEY", "minimax", "MiniMax-M2.7", {}, "oai"),
}

_TOKENS = {v[1]: v[2] for v in RUNGS.values()}


def source_secrets(path="/root/.zo_secrets"):
    try:
        rows = []
        for ln in open(path, encoding="utf-8").read().splitlines():
            ln = ln.strip()
            if not ln or ln.startswith("#") or "=" not in ln:
                continue
            k, _, v = ln.partition("=")
            k = k.strip()
            if k.lower().startswith("export "):
                k = k[7:].strip()
            rows.append((k.lower(), v.strip().strip('"').strip("'")))
        for env, tok in _TOKENS.items():
            if not os.environ.get(env):
                for kl, v in rows:
                    if tok in kl and v:
                        os.environ[env] = v
                        break
    except Exception:
        pass


SYS = ("You are a senior front-end engineer. Build ONE complete, self-contained "
       "HTML file: all CSS and JS inline, no external/CDN dependencies, no build step. "
       "It must be interactive and wired (buttons/inputs actually do something via JS). "
       "Use semantic HTML and aria-labels on controls. Output ONLY the raw HTML "
       "document, starting with <!doctype html>. No markdown fences, no commentary.")


def _extract_html(text):
    if not text:
        return ""
    m = re.search(r"```(?:html)?\s*(.*?)```", text, re.S)
    if m:
        text = m.group(1)
    i = text.lower().find("<!doctype")
    if i == -1:
        i = text.lower().find("<html")
    return text[i:].strip() if i != -1 else text.strip()


def _post(url, headers, body, timeout=180):
    req = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST",
                                 headers={"User-Agent": UA, **headers})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, (e.read().decode("utf-8", "replace") if e.fp else "")
    except Exception as e:
        return -1, f"{type(e).__name__}: {e}"


def build(rung, directive, max_tokens=4000):
    base, key_env, _tok, default_model, extra, kind = RUNGS[rung]
    model = os.environ.get(f"{rung.upper()}_BUILD_MODEL", default_model)
    key = os.environ.get(key_env)
    if not key:
        return {"rung": rung, "model": model, "ok": False, "error": f"no key ({key_env})"}
    t0 = time.time()
    if kind == "anthropic":
        st, bd = _post(f"{base}/v1/messages",
                       {"x-api-key": key, "anthropic-version": "2023-06-01", "content-type": "application/json"},
                       {"model": model, "max_tokens": max_tokens, "system": SYS,
                        "messages": [{"role": "user", "content": directive}]})
        raw = ""
        if st == 200:
            try:
                raw = "".join(c.get("text", "") for c in json.loads(bd).get("content", []) if c.get("type") == "text")
            except Exception as e:
                bd = f"parse: {e}"
    else:
        body = {"model": model, "max_tokens": max_tokens, "temperature": 0.2,
                "messages": [{"role": "system", "content": SYS}, {"role": "user", "content": directive}]}
        body.update(extra)
        st, bd = _post(f"{base}/chat/completions",
                       {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}, body)
        raw = ""
        if st == 200:
            try:
                raw = (json.loads(bd)["choices"][0]["message"].get("content") or "")
            except Exception as e:
                bd = f"parse: {e}"
    dt = round(time.time() - t0, 1)
    if st != 200 or not raw:
        return {"rung": rung, "model": model, "ok": False, "error": f"HTTP {st}: {bd[:160]}", "build_s": dt}
    html = _extract_html(raw)
    return {"rung": rung, "model": model, "ok": bool(html), "html": html,
            "build_s": dt, "html_bytes": len(html)}
