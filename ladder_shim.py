#!/usr/bin/env python3
"""
ladder_shim.py v1.1 -- OpenAI-compatible front door for escalation.py.

Lets Goose (single-provider per session) reach the full 16-rung ladder by
pointing GOOSE's OPENAI_BASE_URL at this shim. The shim is a dumb HTTP->Python
router: escalation.ask() picks up provider keys from os.environ (hydrated by
the vault at bootstrap), so the shim itself holds zero credentials.

v1.1 (2026-05-22): bind 0.0.0.0 not 127.0.0.1.
  Modal containers raise OSError [Errno 99] Cannot assign requested address
  when binding 127.0.0.1, even though loopback CONNECTS fine. All sibling
  services (write_service:8772 etc) bind 0.0.0.0 and are reached via 127.0.0.1.
  Modal does not publicly expose container ports unless explicitly tunneled,
  so 0.0.0.0 here is not network-public. Goose still connects via
  http://127.0.0.1:8796/v1.

Design notes:
  - ask() returns an EscalationResult dataclass, NOT a string. We read
    result.success / result.text.
  - max_tokens passed through (default 8192 so builder code is not truncated).
  - system message routed to ask()'s dedicated `system` param.
  - /health endpoint for `zm check` / supervisord liveness.

Port 8796 (add to port registry).
"""
import os
import subprocess
import sys
import time
import uuid

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import uvicorn
import json
from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from typing import Any, List, Optional

import escalation

SHIM_VERSION = "1.2"
LADDER_MODEL_ID = "zo-ladder-v1"
DEFAULT_TASK = "builder"
FULL_LADDER_ATTEMPTS = 16

# Per-recipe complexity routing lives in escalation.task_for_model(): the model
# id Goose sends (set via GOOSE_MODEL per recipe) selects the ladder start tier,
# so a harder directive no longer flattens to rung 0. See escalation.MODEL_TASK_MAP.

app = FastAPI(title="Zo Mesh Ladder Shim", version=SHIM_VERSION)


class Message(BaseModel):
    role: str
    content: Optional[str] = None
    tool_calls: Optional[list] = None
    tool_call_id: Optional[str] = None
    name: Optional[str] = None


class ChatCompletionRequest(BaseModel):
    model: Optional[str] = LADDER_MODEL_ID
    messages: List[Message]
    temperature: Optional[float] = 0.5
    max_tokens: Optional[int] = 8192
    stream: Optional[bool] = False  # accepted but shim is non-streaming
    tools: Optional[list] = None
    tool_choice: Optional[Any] = None  # pass-through: Pydantic must not drop Goose's tool payload


@app.get("/health")
async def health():
    return {"ok": True, "version": SHIM_VERSION, "model": LADDER_MODEL_ID}


@app.post("/v1/chat/completions")
async def chat_completions(request: ChatCompletionRequest):
    system_parts = [m.content for m in request.messages
                    if m.role == "system" and m.content]
    convo_parts = [f"{m.role.upper()}: {m.content}"
                   for m in request.messages
                   if m.role != "system" and m.content]
    system = "\n\n".join(system_parts) if system_parts else None
    prompt = "\n\n".join(convo_parts) if convo_parts else ""

    task_type = escalation.task_for_model(request.model)

    try:
        result = escalation.ask(
            task_type=task_type,
            prompt=prompt,
            system=system,
            max_tokens=request.max_tokens or 8192,
            temperature=request.temperature if request.temperature is not None else 0.5,
            max_attempts=FULL_LADDER_ATTEMPTS,
            tools=request.tools,
        )
    except Exception as e:
        raise HTTPException(status_code=500,
                            detail=f"ladder exception: {type(e).__name__}: {e}")

    if not result.success or (not result.text and not result.tool_calls):
        raise HTTPException(
            status_code=502,
            detail=f"ladder exhausted: {result.error or 'no text'} | "
                   f"attempts={result.attempts}")

    message = {"role": "assistant", "content": result.text or None}
    finish_reason = "stop"
    if result.tool_calls:
        message["tool_calls"] = result.tool_calls
        finish_reason = "tool_calls"

    cmpl_id = f"chatcmpl-{uuid.uuid4().hex}"
    created = int(time.time())
    model_id = request.model or LADDER_MODEL_ID

    # --- streaming path: Goose (and most agentic clients) request stream:true
    # and parse the body as SSE. Our ladder produces the COMPLETE result at once,
    # so we emit it as one content/tool_calls delta chunk, then [DONE]. ---
    if request.stream:
        def _sse():
            # initial role chunk
            head = {"id": cmpl_id, "object": "chat.completion.chunk",
                    "created": created, "model": model_id,
                    "choices": [{"index": 0, "delta": {"role": "assistant"},
                                 "finish_reason": None}]}
            yield f"data: {json.dumps(head)}\n\n"
            # tool_calls delta (streamed shape needs index on each call)
            if result.tool_calls:
                ARG_CHUNK = 1024  # slice args small: one big SSE data: line
                #                   overflows Goose's transport buffer on >17KB
                #                   file writes -> EOF parse error, write dropped.
                for i, tc in enumerate(result.tool_calls):
                    fn = (tc.get("function") or {})
                    args_str = fn.get("arguments")
                    if not isinstance(args_str, str):
                        args_str = json.dumps(args_str or {})
                    # opening chunk: id/type/name, empty arguments
                    head_tc = {"index": i, "id": tc.get("id"),
                               "type": tc.get("type", "function"),
                               "function": {"name": fn.get("name"), "arguments": ""}}
                    chunk = {"id": cmpl_id, "object": "chat.completion.chunk",
                             "created": created, "model": model_id,
                             "choices": [{"index": 0,
                                          "delta": {"tool_calls": [head_tc]},
                                          "finish_reason": None}]}
                    yield f"data: {json.dumps(chunk)}\n\n"
                    # argument fragments
                    for off in range(0, len(args_str), ARG_CHUNK):
                        frag = args_str[off:off + ARG_CHUNK]
                        piece = {"index": i,
                                 "function": {"arguments": frag}}
                        chunk = {"id": cmpl_id, "object": "chat.completion.chunk",
                                 "created": created, "model": model_id,
                                 "choices": [{"index": 0,
                                              "delta": {"tool_calls": [piece]},
                                              "finish_reason": None}]}
                        yield f"data: {json.dumps(chunk)}\n\n"
            elif result.text:
                chunk = {"id": cmpl_id, "object": "chat.completion.chunk",
                         "created": created, "model": model_id,
                         "choices": [{"index": 0,
                                      "delta": {"content": result.text},
                                      "finish_reason": None}]}
                yield f"data: {json.dumps(chunk)}\n\n"
            # final chunk with finish_reason
            tail = {"id": cmpl_id, "object": "chat.completion.chunk",
                    "created": created, "model": model_id,
                    "choices": [{"index": 0, "delta": {},
                                 "finish_reason": finish_reason}]}
            yield f"data: {json.dumps(tail)}\n\n"
            yield "data: [DONE]\n\n"
        return StreamingResponse(_sse(), media_type="text/event-stream")

    if result.tool_calls:
        message["tool_calls"] = result.tool_calls
        finish_reason = "tool_calls"
    return {
        "id": f"chatcmpl-{uuid.uuid4().hex}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": request.model or LADDER_MODEL_ID,
        "choices": [{
            "index": 0,
            "message": message,
            "finish_reason": finish_reason,
        }],
        "x_zo_backend": result.backend,
        "x_zo_model": result.model,
        "x_zo_task": task_type,
    }


KEY_HYDRATOR = "/home/workspace/zo_mesh/key_hydrator.py"
HYDRATE_KEYS = ("MINIMAX_API_KEY", "GEMINI_API_KEY", "ANTHROPIC_API_KEY")


def _self_hydrate_keys(kh=KEY_HYDRATOR, runner=None, attempts=3, sleep=time.sleep):
    """Resolve the LLM keys into THIS process's env via the canonical
    key_hydrator, so the shim is keyed HOWEVER it's launched -- a bare
    `python3 ladder_shim.py`, a crash/watchdog restart, anything -- not only via
    ladder_shim_with_keys.sh. Without this, escalation.ask() sees
    'RcGeminiAPIKey not set' and every rung above MiniMax 502s (the recurring
    regression). In-process mirror of that wrapper, plus a short retry loop for
    the boot-race where the vault/Tower isn't ready on the first pass (same
    reason key_hydrator retries). `runner`/`sleep` are injectable for tests."""
    runner = runner or (lambda k: subprocess.run(
        ["python3", kh, "--get", k], capture_output=True, text=True, timeout=30).stdout.strip())

    def _pull():
        for k in HYDRATE_KEYS:
            if os.environ.get(k):
                continue  # already injected (Modal canonical / wrapper) -- keep it
            try:
                v = runner(k)
                if v:
                    os.environ[k] = v
            except Exception as e:  # never let hydration crash the shim
                print(f"[ladder_shim] self-hydrate {k} failed: {e}", file=sys.stderr)
        # escalation checks RcGeminiAPIKey first, then GEMINI_API_KEY -- mirror it.
        if os.environ.get("GEMINI_API_KEY") and not os.environ.get("RcGeminiAPIKey"):
            os.environ["RcGeminiAPIKey"] = os.environ["GEMINI_API_KEY"]

    for i in range(max(1, attempts)):
        _pull()
        if os.environ.get("RcGeminiAPIKey"):  # the rung-unblocker
            print(f"[ladder_shim] keys hydrated (gemini present after pass {i + 1})",
                  file=sys.stderr)
            return True
        if i + 1 < attempts:
            sleep(3)
    print("[ladder_shim] WARNING: RcGeminiAPIKey still unresolved after self-hydrate "
          "-- gemini/gemma rungs will 502", file=sys.stderr)
    return False


if __name__ == "__main__":
    _self_hydrate_keys()   # keyed regardless of launch path (bare or wrapper)
    # 0.0.0.0 required on Modal (see v1.1 note). Reached via 127.0.0.1 loopback.
    uvicorn.run(app, host="0.0.0.0", port=8796, log_level="info")