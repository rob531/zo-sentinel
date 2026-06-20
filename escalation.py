#!/usr/bin/env python3
"""
escalation.py v0.9 -- unified inference ladder for ZO-Sentinel.

v0.9 (2026-05-30): Complexity-routed start tiers + model->task mapping.
  Until now every TASK_START_TIER entry was 0 and the ladder_shim flattened
  every request to task_type="builder", so EVERY goose build started (and,
  since MiniMax always returns non-empty, ended) at rung 0 -- the 16-rung
  ladder was dead weight. Added builder_{low,medium,high,critical} start tiers
  (0 / 1 / 10 / 14) and MODEL_TASK_MAP + task_for_model() so goose_runner can
  route a harder directive to a HIGHER rung via its recipe's GOOSE_MODEL alias
  (zo-ladder-{low,medium,high,critical}). Fully back-compatible: unknown/legacy
  model ids -> "builder" -> rung 0, so existing callers are unchanged. The
  escalation success test (text or tool_calls) is unchanged -- this only moves
  the START rung, it does not add a quality gate (see ladder_quality follow-on).

v0.8 (2026-04-30): Gemma 3 rungs added.
  Empirical evidence from gemma_probe v1.1 (gemma_20260430T155105Z.json):
    - 4 Gemma 3 models REACHABLE via Pixel RcGeminiAPIKey, free tier:
      gemma-3-4b-it (2.1s), gemma-3-12b-it (1.7s), gemma-3-27b-it (2.9s),
      gemma-4-31b-it (32s but produces non-code reasoning output -- skipped)
    - Gemma 4 26b-a4b times out reliably -- skipped
    - Gemma 4 31b produces 'Role: ... Constraint 1: ...' reasoning text
      instead of code despite system prompt -- skipped
    - Critical payload format gap: Gemma REJECTS systemInstruction field
      (HTTP 400 across the board); requires system inlined into user
      prompt. Existing Gemini adapter sends systemInstruction.

  CHANGES vs v0.7:
    1. _call_gemini_direct now branches on model_id prefix. If 'gemma-',
       inlines system into user prompt (no systemInstruction field).
       Gemini models behave exactly as before.
    2. LADDER inserts 3 Gemma 3 rungs at positions 7-9, between the last
       Gemini-direct rung and the first Zo-routed rung. Order: 12B (fastest),
       4B (smallest), 27B (largest reliable). Position chosen so Gemma
       acts as a free fallback breadth between Gemini direct and Zo paid.
    3. Other rungs renumbered: Zo-free now 10-11, Zo-subscriber 12-15.
    4. Gemma rungs use rpm_limit=15 conservatively. Google AI Studio's
       documented Gemma rate limits are higher (15-30 RPM depending on
       model) but conservative is correct on first deployment.

  WHY GEMMA AT POSITION 7-9 (NOT EARLIER):
    - Gemma is small (4B-27B params) compared to Gemini Flash (~70B
      effective). Lower quality on hard prompts.
    - But Gemma is FAST (1.7-2.9s) and FREE.
    - Right place: when MiniMax + 6 Gemini variants ALL fail, Gemma is the
      next-cheapest option before burning Zo paid credit.

  WHAT IS NOT CHANGED:
    - TASK_START_TIER unchanged (all start at rung 0 = MiniMax)
    - Rate limiter, budget tracker, _log_call unchanged
    - ask() signature unchanged
    - max_tokens defaulting -- still 4096
    - All existing strippers (_strip_reasoning_preamble, _strip_code_fences)
      apply uniformly to Gemma output

  VALIDATION PATH:
    - In production: builder/wisdom_synth typically land on rung 0 (MiniMax)
      and never reach Gemma. To force-test, drop a request that exhausts
      MiniMax + Gemini (e.g. set TASK_START_TIER for some test task to 7).
    - The escalation logger writes per-call telemetry to mesh_memory; first
      Gemma call surfaces there if it ever happens. The Gemma rungs are
      INSURANCE, not primary traffic.

v0.7 (2026-04-30): Lifted fence-strip from zo_sentinel_builder.py to
  complete the parity story.
v0.6 (2026-04-29): Lifted reasoning-strip apparatus from builder.py.
  MiniMax adapter restored reasoning_split=True. Gemini adapter
  added responseMimeType='text/plain'.
v0.5 (2026-04-28): wisdom_synthesis start tier 9 -> 0 to dodge Zo
  paid-tier 402 Payment Required and use free MiniMax direct first.
v0.4 (2026-04-23): MiniMax adapter removed reasoning_split (regression
  fixed in v0.6). Inline <think> stripping kept as belt-and-suspenders.
v0.3 (2026-04-23): Corrected Gemini model IDs from live models endpoint.
v0.2 (2026-04-23): Fixed MiniMax URL (minimaxi.com -> minimax.io).
v0.1 (2026-04-22): Initial build.
"""

import os
import sys
import json
import re
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional


# ---------------------------------------------------------------------------
# REASONING-MODE STRIPPER (lifted verbatim from zo_sentinel_builder.py
# 2026-04-21). Same docstring/code as v0.7.
# ---------------------------------------------------------------------------

# LLM-payload sanitizers now live in minimax_utils.py (the canonical, consolidated
# copy -- byte-identical logic). Imported under the existing private names so every
# call site in this module is unchanged. minimax_utils is stdlib-only and ships in
# the same package, so this import is safe even on the build-critical ladder path.
from minimax_utils import (  # noqa: E402
    strip_reasoning as _strip_reasoning_preamble,
    strip_code_fences as _strip_code_fences,
    normalize as _normalize_response,
    _REASONING_CLOSED_RX, _REASONING_OPEN_RX, _CODE_START_MARKERS,
)


# ---------------------------------------------------------------------------
# Ladder spec
# ---------------------------------------------------------------------------

@dataclass
class ModelSpec:
    backend: str
    model_id: str
    context_window: int
    rpm_limit: int
    cost_priority: float
    label: str
    base_url: str = ""        # for openai_compatible rungs
    key_env: str = ""         # env var holding the API key
    extra_params: dict = field(default_factory=dict)  # provider-specific tuning
                                                       # (e.g. gpt-oss reasoning_effort)


LADDER = [
    # Tier 0 -- MiniMax direct, flat $10/mo
    ModelSpec("minimax_direct", "MiniMax-M2.7",                   205_000, 60, 0.0,
              "MiniMax 2.7 (direct, flat $10/mo)"),

    # Tier 0b -- MiniMax M3 direct (stronger coder; same key, 200 RPM). The pinned
    # builder for complexity=medium (builder_medium starts HERE) -- a MiniMax
    # primary so it returns non-empty and does NOT escalate mid-build (#73); 429s
    # back off + retry the same rung (see _call_minimax_direct).
    ModelSpec("minimax_direct", "MiniMax-M3",                     205_000, 200, 0.0,
              "MiniMax M3 (direct, stronger coder, 200 RPM)"),

    # Tier 1a -- Gemini stable free-tier (confirmed RPM allocations)
    ModelSpec("gemini_direct",  "gemini-2.5-flash-lite",        1_000_000, 10, 0.0,
              "Gemini 2.5 Flash Lite (free, ~10 RPM) -- CONFIRMED"),
    ModelSpec("gemini_direct",  "gemini-2.0-flash-lite",        1_000_000, 30, 0.0,
              "Gemini 2.0 Flash Lite (free, ~30 RPM)"),
    ModelSpec("gemini_direct",  "gemini-2.5-flash",             1_000_000, 10, 0.0,
              "Gemini 2.5 Flash (free, ~10 RPM)"),
    ModelSpec("gemini_direct",  "gemini-2.0-flash",             1_000_000, 15, 0.0,
              "Gemini 2.0 Flash (free, ~15 RPM)"),

    # Tier 1b -- Gemini preview (may be 0 RPM on free tier, fall through gracefully)
    ModelSpec("gemini_direct",  "gemini-3.1-flash-lite-preview", 1_000_000, 15, 0.0,
              "Gemini 3.1 Flash Lite Preview (15 RPM if quota granted)"),
    ModelSpec("gemini_direct",  "gemini-3-flash-preview",        1_000_000,  5, 0.0,
              "Gemini 3 Flash Preview (5 RPM if quota granted)"),

    # Tier 1c -- Gemma 3 direct via same Pixel key (NEW v0.8)
    # Confirmed reachable via gemma_probe v1.1 2026-04-30. Each requires
    # gemma-format payload (no systemInstruction); adapter detects by prefix.
    # Order: 12B fastest, 4B smallest, 27B largest reliable.
    ModelSpec("gemini_direct",  "gemma-3-12b-it",                  128_000, 15, 0.0,
              "Gemma 3 12B Instruct (free, 1.7s, 128K ctx)"),
    ModelSpec("gemini_direct",  "gemma-3-4b-it",                   128_000, 15, 0.0,
              "Gemma 3 4B Instruct (free, 2.1s, 128K ctx)"),
    ModelSpec("gemini_direct",  "gemma-3-27b-it",                  128_000, 15, 0.0,
              "Gemma 3 27B Instruct (free, 2.9s, 128K ctx)"),

    # Tier 2 -- Zo free models
    ModelSpec("zo_routed", "zo:openai/gpt-5.4-mini",             400_000, 60, 0.0,
              "GPT-5.4 mini (Zo free, 400K ctx)"),
    ModelSpec("zo_routed", "zo:zai/glm-5",                       203_000, 60, 0.0,
              "GLM-5 (Zo free)"),

    # Tier 3 -- Zo subscriber (burns Zo credits)
    ModelSpec("zo_routed", "zo:google/gemini-3.1-pro-preview",   1_000_000, 60, 1.0,
              "Gemini 3.1 Pro (Zo sub, 1.0x)"),
    ModelSpec("zo_routed", "zo:openai/gpt-5.4",                  1_000_000, 60, 1.1,
              "GPT-5.4 (Zo sub, 1.1x)"),
    ModelSpec("zo_routed", "zo:anthropic/claude-sonnet-4-5",     1_000_000, 60, 2.1,
              "Sonnet 4.5 (Zo sub, 2.1x)"),
    ModelSpec("zo_routed", "zo:anthropic/claude-opus-4-7",       1_000_000, 60, 3.0,
              "Opus 4.7 (Zo sub, 3.0x)"),

    # Tier 4 -- NVIDIA NIM (free dev credits; OpenAI SDK-compatible; tool-capable).
    # APPENDED LAST so it never shifts the index-based TASK_START_TIER map. Reached
    # on demand via GOOSE_MODEL=zo-ladder-nvidia (task "builder_nvidia"); NOT in the
    # default builder path until promoted. Model overridable via NVIDIA_BUILD_MODEL.
    # Uses the generic openai_compatible adapter (base_url + key_env on the spec).
    ModelSpec("openai_compatible",
              os.environ.get("NVIDIA_BUILD_MODEL", "mistralai/mistral-nemotron"),
              131_000, 40, 0.0,
              "NVIDIA NIM (free credits, OpenAI-compat, tool-calling)",
              base_url="https://integrate.api.nvidia.com/v1", key_env="NVIDIA_API_KEY"),

    # Tier 4b -- Cerebras (free ~14.4k req/day, no card; OpenAI-compatible; very
    # high tok/s). gpt-oss-120b supports function/parallel tool calling -> can drive
    # goose. Opt-in via GOOSE_MODEL=zo-ladder-cerebras (task builder_cerebras); NOT
    # in the default path until promoted. Model overridable via CEREBRAS_BUILD_MODEL.
    ModelSpec("openai_compatible",
              os.environ.get("CEREBRAS_BUILD_MODEL", "gpt-oss-120b"),
              131_072, 30, 0.2,
              "Cerebras gpt-oss-120b (free ~14.4k rpd, OpenAI-compat, tool-calling)",
              base_url="https://api.cerebras.ai/v1", key_env="CEREBRAS_API_KEY",
              extra_params={"reasoning_effort": "medium"}),

    # Tier 4c -- Mistral La Plateforme (free tier; OpenAI-compatible; Codestral is a
    # coding-specialist with tool-calling). Opt-in via GOOSE_MODEL=zo-ladder-mistral
    # (task builder_mistral). Model overridable via MISTRAL_BUILD_MODEL.
    ModelSpec("openai_compatible",
              os.environ.get("MISTRAL_BUILD_MODEL", "codestral-latest"),
              256_000, 30, 0.2,
              "Mistral Codestral (free tier, OpenAI-compat, tool-calling)",
              base_url="https://api.mistral.ai/v1", key_env="MISTRAL_API_KEY"),
]

TASK_START_TIER = {
    "directive_gen":     0,
    "builder":           0,
    "generate":          0,
    "signal_enrichment": 0,
    "classification":    0,
    "wisdom_synthesis":  0,
    "default":           0,
    "embedding":        -1,
    # --- Complexity-routed builder tiers --------------------------------------
    # goose_runner picks a recipe / model alias per directive complexity; the
    # ladder_shim maps that alias to one of these task types so a HARDER
    # directive STARTS higher up the ladder instead of every build flattening
    # to rung 0 (MiniMax). Cost-ordered: prefer free rungs, escalate on failure.
    #   rung 0  = MiniMax M2.7 (free, flat)
    #   rung 1  = MiniMax M3 (free, flat, 200 RPM -- stronger coder)
    #   rung 2  = Gemini 2.5 flash-lite (free, 1M ctx)
    #   rung 11 = zo:openai/gpt-5.4-mini (free, strong)
    #   rung 15 = zo:anthropic/claude-sonnet-4-5 (PAID; explicit opt-in only)
    "builder_low":       0,
    "builder_medium":    1,
    "builder_high":     11,
    "builder_critical": 15,
    "builder_nvidia":   17,   # NVIDIA NIM rung; opt-in capacity/quality
    "builder_cerebras": 18,   # Cerebras gpt-oss-120b rung; opt-in capacity (free, fast)
    "builder_mistral":  19,   # Mistral Codestral rung; opt-in capacity (free, coder)
}

# Cost gate: which task types may spend on PAID rungs (cost_priority > 0).
# Everything else is HARD-capped to the free rungs (0-11) -- a non-critical build
# never burns paid Zo credit even if all its free rungs fail (it just exhausts
# and the caller falls back), instead of cascading into gpt-5.4 / sonnet / opus.
# Env-tunable (comma-separated task types). Default: only builder_critical.
PAID_OK_TASKS = {t.strip() for t in
                 os.environ.get("LADDER_PAID_OK_TASKS", "builder_critical").split(",")
                 if t.strip()}

# Maps the OpenAI `model` field a caller (Goose, via GOOSE_MODEL per recipe)
# sends through the ladder_shim to one of the task types above. Lives here, with
# the ladder, so it is testable without importing the shim's fastapi/uvicorn
# stack. Unknown/legacy ids -> "builder" -> rung 0, so existing callers that
# send "zo-ladder-v1" / "MiniMax-Text-01" are unaffected.
MODEL_TASK_MAP = {
    "zo-ladder-v1":       "builder",
    "zo-ladder-low":      "builder_low",
    "zo-ladder-medium":   "builder_medium",
    "zo-ladder-high":     "builder_high",
    "zo-ladder-critical": "builder_critical",
    "zo-ladder-nvidia":   "builder_nvidia",
    "zo-ladder-cerebras": "builder_cerebras",
    "zo-ladder-mistral":  "builder_mistral",
}


def task_for_model(model_id: Optional[str]) -> str:
    """Resolve the escalation task_type for an incoming model id."""
    return MODEL_TASK_MAP.get((model_id or "").strip(), "builder")

DAILY_PRIORITY_BUDGET = 5.0


class _RateLimiter:
    def __init__(self):
        self._windows: dict[str, deque] = {}

    def _prune(self, dq, now):
        cutoff = now - 60.0
        while dq and dq[0] < cutoff:
            dq.popleft()

    def available(self, model_id, rpm_limit):
        now = time.monotonic()
        dq = self._windows.get(model_id)
        if dq is None: return True
        self._prune(dq, now)
        return len(dq) < rpm_limit

    def record(self, model_id):
        now = time.monotonic()
        dq = self._windows.setdefault(model_id, deque())
        self._prune(dq, now)
        dq.append(now)

_limiter = _RateLimiter()


class _BudgetTracker:
    def __init__(self, cap):
        self._cap = cap
        self._day = ""
        self._spent = 0.0

    def _refresh(self):
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        if today != self._day:
            self._day = today
            self._spent = 0.0

    def can_spend(self, p):
        self._refresh()
        return (self._spent + p) <= self._cap

    def record(self, p):
        self._refresh()
        self._spent += p

    def status(self):
        self._refresh()
        return {"day": self._day, "spent": round(self._spent, 3),
                "cap": self._cap, "remaining": round(self._cap - self._spent, 3)}

_budget = _BudgetTracker(DAILY_PRIORITY_BUDGET)


# ---------------------------------------------------------------------------
# Backend adapters (v0.8: gemini adapter branches on gemma- prefix)
# ---------------------------------------------------------------------------

_MINIMAX_429_RETRIES = 4


def _retry_after_secs(resp, default: float) -> float:
    """Backoff seconds for a 429: prefer the Retry-After header, else `default`."""
    ra = resp.headers.get("Retry-After") or resp.headers.get("retry-after")
    if ra:
        try:
            return max(0.0, float(ra))
        except (TypeError, ValueError):
            pass
    return float(default)


# MiniMax sometimes emits tool calls as its native <minimax:tool_call> XML
# envelope inside message.content instead of the OpenAI tool_calls field. Goose
# acts only on structured tool_calls, so we convert the envelope (used by
# _call_minimax_direct). Regexes compiled once at module load.
_MMX_BLOCK_RX = re.compile(r"<minimax:tool_call>(.*?)</minimax:tool_call>", re.DOTALL)
_MMX_INVOKE_RX = re.compile(r'<invoke\s+name="([^"]+)"\s*>(.*?)</invoke>', re.DOTALL)
_MMX_PARAM_RX = re.compile(r'<parameter\s+name="([^"]+)"\s*>(.*?)</parameter>', re.DOTALL)


def _parse_minimax_tool_calls(content):
    """Parse MiniMax's native <minimax:tool_call> envelope from `content` into
    OpenAI tool_calls. Returns (tool_calls | None, residual_text) where
    residual_text is `content` with the envelope(s) stripped (surrounding prose
    preserved). Never raises: on any miss it returns (None, content), so the
    adapter behaves exactly as before when there is no envelope."""
    if not content or "<minimax:tool_call>" not in content:
        return None, content
    tool_calls = []
    for block in _MMX_BLOCK_RX.findall(content):
        for name, body in _MMX_INVOKE_RX.findall(block):
            args = {k: v.strip() for k, v in _MMX_PARAM_RX.findall(body)}
            tool_calls.append({
                "id": f"call_{uuid.uuid4().hex[:24]}",
                "type": "function",
                "function": {"name": name.strip(), "arguments": json.dumps(args)},
            })
    residual = _MMX_BLOCK_RX.sub("", content).strip()
    return (tool_calls or None), residual


def _call_minimax_direct(spec, prompt, system, max_tokens, temperature, tools=None):
    import requests
    key = os.environ.get("MINIMAX_API_KEY")
    if not key:
        return None, "MINIMAX_API_KEY not set", None
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})
    payload = {"model": spec.model_id,
               "messages": messages,
               "max_tokens": max_tokens,
               "temperature": temperature,
               "reasoning_split": True}
    if tools:
        payload["tools"] = tools
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    url = "https://api.minimax.io/v1/chat/completions"
    # M3 (200 RPM) returns 429 + a backoff header when rate-limited. Back off and
    # retry the SAME model rather than letting the ladder escalate goose's builder
    # mid-build (the #73 coherence guarantee). Bounded; on exhaustion, surface the
    # error so the ladder can fall through as a last resort. (Helps M2.7 too.)
    for attempt in range(_MINIMAX_429_RETRIES + 1):
        try:
            r = requests.post(url, headers=headers, json=payload, timeout=240)
        except Exception as e:
            return None, f"{type(e).__name__}: {e}", None
        if r.status_code == 429:
            if attempt < _MINIMAX_429_RETRIES:
                time.sleep(min(_retry_after_secs(r, 2.0 * (attempt + 1)), 30.0))
                continue
            return None, "429 rate-limited (backoff exhausted)", None
        try:
            r.raise_for_status()
            choices = r.json().get("choices", [])
            if not choices:
                return None, "empty choices", None
            msg  = choices[0].get("message", {})
            raw  = (msg.get("content", "") or "").strip()
            tool_calls = msg.get("tool_calls")
            content = _normalize_response(raw) if raw else ""
            # MiniMax may put the call in content as <minimax:tool_call>
            # XML rather than the structured tool_calls field. Goose acts
            # only on structured tool_calls, so convert; prefer a real
            # structured field if MiniMax already provided one.
            if not tool_calls and content and "<minimax:tool_call>" in content:
                _parsed, _residual = _parse_minimax_tool_calls(content)
                if _parsed:
                    tool_calls = _parsed
                    content = _residual
            if not content and not tool_calls:
                return None, "empty content and no tools", None
            return (content or None), None, tool_calls
        except Exception as e:
            return None, f"{type(e).__name__}: {e}", None
    return None, "429 retries exhausted", None


def _openai_tools_to_gemini(tools):
    """Translate OpenAI `tools` (the function specs goose sends) into Gemini
    functionDeclarations. None if there are no usable tools. Lets a Gemini rung
    DRIVE goose's developer extension instead of returning prose (the gap that
    pinned tool-calling to MiniMax only)."""
    decls = []
    for t in tools or []:
        fn = (t or {}).get("function") if isinstance(t, dict) else None
        if not fn or not fn.get("name"):
            continue
        d = {"name": fn["name"]}
        if fn.get("description"):
            d["description"] = fn["description"]
        if isinstance(fn.get("parameters"), dict):
            d["parameters"] = fn["parameters"]
        decls.append(d)
    return [{"functionDeclarations": decls}] if decls else None


def _gemini_parts_to_result(parts):
    """Parse Gemini response `parts` -> (text, tool_calls|None): functionCall
    parts become OpenAI tool_calls; text parts are concatenated."""
    tool_calls = []
    text_chunks = []
    for p in parts or []:
        if not isinstance(p, dict):
            continue
        if p.get("functionCall"):
            fc = p["functionCall"] or {}
            tool_calls.append({
                "id": f"call_{uuid.uuid4().hex[:24]}",
                "type": "function",
                "function": {"name": fc.get("name", ""),
                             "arguments": json.dumps(fc.get("args") or {})},
            })
        elif isinstance(p.get("text"), str):
            text_chunks.append(p["text"])
    return "".join(text_chunks).strip(), (tool_calls or None)


def _call_gemini_direct(spec, prompt, system, max_tokens, temperature, tools=None):
    """v0.8: branches on model_id prefix.

    Gemini models accept systemInstruction. Gemma models REJECT it (HTTP 400)
    and require system inlined into the user prompt. The branch is at the
    payload-construction level so the rest of the call (URL, headers, response
    parsing, normalize) is unified.
    """
    import requests
    key = os.environ.get("RcGeminiAPIKey") or os.environ.get("GEMINI_API_KEY")
    if not key:
        return None, "RcGeminiAPIKey not set", None
    url = (f"https://generativelanguage.googleapis.com/v1beta/models/"
           f"{spec.model_id}:generateContent")

    is_gemma = spec.model_id.startswith("gemma-")
    # Tool-calling: forward goose's tools as Gemini functionDeclarations (gemini
    # models only; gemma function-calling is not reliably supported -> text-only).
    gem_tools = None if is_gemma else _openai_tools_to_gemini(tools)
    gen_cfg = {"maxOutputTokens": max_tokens, "temperature": temperature}
    if not gem_tools:
        # responseMimeType text/plain SUPPRESSES functionCall parts -> only set it
        # when we are NOT function-calling.
        gen_cfg["responseMimeType"] = "text/plain"
    if is_gemma:
        combined_prompt = f"{system}\n\n{prompt}" if system else prompt
        payload = {"contents": [{"parts": [{"text": combined_prompt}]}],
                   "generationConfig": gen_cfg}
    else:
        payload = {"contents": [{"parts": [{"text": prompt}]}],
                   "generationConfig": gen_cfg}
        if system:
            payload["systemInstruction"] = {"parts": [{"text": system}]}
        if gem_tools:
            payload["tools"] = gem_tools

    try:
        # Gemma can be a touch slower than Gemini on cold-start; use 90s
        # for Gemma, 60s for Gemini (matches v0.7 default).
        timeout = 90 if is_gemma else 60
        r = requests.post(url,
            headers={"Content-Type": "application/json",
                     "X-Goog-Api-Key": key},
            json=payload, timeout=timeout)
        r.raise_for_status()
        cands = r.json().get("candidates") or []
        if not cands:
            return None, "no candidates", None
        parts = ((cands[0].get("content") or {}).get("parts")) or []
        text, tool_calls = _gemini_parts_to_result(parts)
        text = _normalize_response(text) if text else ""
        if tool_calls:                      # NATIVE Gemini function-calling -> drives goose
            return (text or None), None, tool_calls
        return (text or None), (None if text else "empty"), None
    except Exception as e:
        return None, f"{type(e).__name__}: {e}", None


def _call_zo_routed(spec, prompt, system, max_tokens, temperature, tools=None):
    import requests
    token = os.environ.get("ZO_CLIENT_IDENTITY_TOKEN")
    if not token:
        return None, "ZO_CLIENT_IDENTITY_TOKEN not set", None
    full_prompt = f"{system}\n\n{prompt}" if system else prompt
    try:
        r = requests.post(
            "https://api.zo.computer/zo/ask",
            headers={"Authorization": token,
                     "Content-Type": "application/json"},
            json={"input": full_prompt, "model_name": spec.model_id,
                  "stream": False},
            timeout=120)
        r.raise_for_status()
        text = (r.json().get("output", "") or "").strip()
        text = _normalize_response(text)
        return (text or None), (None if text else "empty"), None
    except Exception as e:
        return None, f"{type(e).__name__}: {e}", None


_OAI_USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")


def _call_openai_compatible(spec, prompt, system, max_tokens, temperature, tools=None):
    """Generic OpenAI SDK-compatible chat/completions rung. base_url + key_env come
    from the ModelSpec, so adding an OpenAI-compatible provider (NVIDIA NIM, Cerebras,
    Groq, GitHub Models, ...) is a LADDER config row, NOT new code. Forwards goose's
    `tools` and reads back structured `tool_calls` (the proven minimax_direct pattern).
    Returns (text, error, tool_calls)."""
    import requests
    key = os.environ.get(spec.key_env) if spec.key_env else None
    if not key:
        return None, f"{spec.key_env or 'key'} not set", None
    base = (spec.base_url or "").rstrip("/")
    if not base:
        return None, "no base_url on spec", None
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})
    payload = {"model": spec.model_id, "messages": messages,
               "max_tokens": max_tokens, "temperature": temperature}
    if spec.extra_params:                      # provider-specific tuning (reasoning_effort, ...)
        payload.update(spec.extra_params)
    if tools:
        payload["tools"] = tools
    try:
        r = requests.post(f"{base}/chat/completions",
                          headers={"Authorization": f"Bearer {key}",
                                   "Content-Type": "application/json",
                                   # browser UA: Cerebras (Cloudflare) 403s the
                                   # default python-requests UA with error 1010.
                                   "User-Agent": _OAI_USER_AGENT},
                          json=payload, timeout=240)
        if r.status_code == 429:
            return None, "429 rate-limited", None
        r.raise_for_status()
        choices = r.json().get("choices", [])
        if not choices:
            return None, "empty choices", None
        msg = choices[0].get("message", {}) or {}
        raw = (msg.get("content", "") or "").strip()
        tool_calls = msg.get("tool_calls")
        content = _normalize_response(raw) if raw else ""
        if not content and not tool_calls:
            return None, "empty content and no tools", None
        return (content or None), None, tool_calls
    except Exception as e:
        return None, f"{type(e).__name__}: {e}", None


BACKEND_ADAPTERS = {
    "minimax_direct": _call_minimax_direct,
    "gemini_direct":  _call_gemini_direct,
    "zo_routed":      _call_zo_routed,
    "openai_compatible": _call_openai_compatible,
}


def _log_call(backend, model, task_type, prompt_chars, response_chars,
              priority, latency_ms, success, error):
    import requests
    try:
        requests.post("http://127.0.0.1:8772/write", json={
            "table": "mesh_memory",
            "rows": [{"agent_id": "escalation.router",
                      "memory_type": "escalation_call",
                      "content": json.dumps({
                          "backend": backend, "model": model,
                          "task_type": task_type,
                          "prompt_chars": prompt_chars,
                          "response_chars": response_chars,
                          "priority": priority, "latency_ms": latency_ms,
                          "success": success,
                          "error": (error or "")[:500],
                          "ts": datetime.now(timezone.utc).isoformat(),
                      }), "importance": 0.3}],
        }, timeout=5)
    except Exception:
        pass


@dataclass
class EscalationResult:
    text: str
    backend: str
    model: str
    priority: float
    latency_ms: int
    attempts: list = field(default_factory=list)
    success: bool = True
    error: Optional[str] = None
    tool_calls: Optional[list] = None


def ask(task_type: str, prompt: str, system: Optional[str] = None,
        max_tokens: int = 4096, temperature: float = 0.5,
        max_attempts: int = 4, tools: Optional[list] = None) -> EscalationResult:
    start_idx = TASK_START_TIER.get(task_type, TASK_START_TIER["default"])
    if start_idx < 0:
        return EscalationResult("", "", "", 0.0, 0, success=False,
                                error=f"'{task_type}' has no escalation path")
    attempts = []
    end_idx = min(start_idx + max_attempts, len(LADDER))
    for i in range(start_idx, end_idx):
        spec = LADDER[i]
        if not _limiter.available(spec.model_id, spec.rpm_limit):
            attempts.append((spec.model_id, "rate_limited", ""))
            continue
        if spec.cost_priority > 0 and task_type not in PAID_OK_TASKS:
            attempts.append((spec.model_id, "cost_gated",
                             f"paid rung blocked for {task_type}"))
            continue
        if spec.cost_priority > 0 and not _budget.can_spend(spec.cost_priority):
            attempts.append((spec.model_id, "budget_exceeded",
                             f"cap={_budget.status()['cap']}"))
            continue
        t0 = time.monotonic()
        text, error, tool_calls = BACKEND_ADAPTERS[spec.backend](
            spec, prompt, system, max_tokens, temperature, tools)
        latency_ms = int((time.monotonic() - t0) * 1000)
        _limiter.record(spec.model_id)
        if text or tool_calls:
            if spec.cost_priority > 0:
                _budget.record(spec.cost_priority)
            _log_call(spec.backend, spec.model_id, task_type,
                      len(prompt), len(text or ""), spec.cost_priority,
                      latency_ms, True, None)
            return EscalationResult(text=text or "", backend=spec.backend,
                                    model=spec.model_id, priority=spec.cost_priority,
                                    latency_ms=latency_ms, attempts=attempts,
                                    success=True, tool_calls=tool_calls)
        _log_call(spec.backend, spec.model_id, task_type,
                  len(prompt), 0, spec.cost_priority, latency_ms, False, error)
        attempts.append((spec.model_id, "call_failed", (error or "")[:100]))
    return EscalationResult("", "", "", 0.0, 0, attempts=attempts,
                            success=False,
                            error=f"all {len(attempts)} attempts exhausted")


def _dry_run():
    print("=" * 76)
    print("ESCALATION LADDER v0.8")
    print("=" * 76)
    print(f"Budget: {DAILY_PRIORITY_BUDGET}/day | {_budget.status()}")
    print(f"\n{'#':<3} {'Backend':<16} {'Model':<40} {'RPM':>4} Cost")
    print("-" * 72)
    for i, s in enumerate(LADDER):
        cost = "FREE" if s.cost_priority == 0 else f"{s.cost_priority:.1f}x"
        print(f"{i:<3} {s.backend:<16} {s.model_id:<40} {s.rpm_limit:>4} {cost}")
    print("\nTask routing:")
    for task, idx in TASK_START_TIER.items():
        t = f"tier {idx}: {LADDER[idx].label}" if idx >= 0 else "dedicated"
        print(f"  {task:<22} -> {t}")
    print("\nEnv:")
    for v in ("MINIMAX_API_KEY", "RcGeminiAPIKey", "ZO_CLIENT_IDENTITY_TOKEN"):
        print(f"  {v:<32} {'SET' if os.environ.get(v) else 'MISSING'}")


def _live_test():
    print("LIVE TEST v0.8 -- MiniMax should hit first with zero fallthrough")
    r = ask("directive_gen", "Reply with exactly the single word: pong",
            max_tokens=16)
    print(f"  backend:  {r.backend}")
    print(f"  model:    {r.model}")
    print(f"  success:  {r.success}")
    print(f"  latency:  {r.latency_ms}ms")
    print(f"  text:     {r.text!r}")
    if r.attempts:
        print("  fallthrough (should be empty):")
        for a in r.attempts:
            print(f"    {a}")
    if not r.success:
        sys.exit(1)
    verdict = ("MiniMax FIRST-TRY PASS"
               if r.backend == "minimax_direct"
               else f"WARNING: MiniMax still failing, used {r.backend}/{r.model}")
    print(f"\n{verdict}")
    print("Ladder live-test PASS.")


if __name__ == "__main__":
    if "--live" in sys.argv:
        _live_test()
    else:
        _dry_run()

# --- ladder_extensions wire-in (added by _wire_ladder_extensions.py) ---
try:
    import ladder_extensions  # noqa: F401
except ImportError:
    pass
# --- end ladder_extensions wire-in ---