"""Pluggable repairers. Same interface: ``repair(task) -> RepairResult``.

OracleRepairer
    Applies the KNOWN inverse of the corruption. By construction it must score
    fidelity_lev == 0.0 and pass. This is the GREEN pole of the self-test.

SloppyRepairer
    Restores correctness, then rewrites the ENCLOSING FUNCTION the way an
    over-editing model does: re-emits it through ``ast.unparse`` (comments
    gone, quoting and spacing normalised), renames every local variable, and
    wraps the body in a defensive ``try/except Exception: raise`` it did not
    need. Semantics are preserved (the re-raise is transparent), so the tests
    pass, yet fidelity_lev is high and delta_cc > 0. This is the RED pole: it
    proves the metrics discriminate rather than merely return a number.

AnthropicRepairer(preservation: bool)
    A real model call over the Messages API using stdlib ``urllib`` only (the
    ``anthropic`` SDK is deliberately NOT imported -- zero new dependencies).
    Two prompt variants that differ ONLY by the preservation instruction.
    Output tokens are capped per call, total calls are capped per instance,
    and cumulative usage plus an estimated cost are tracked and printable.
    The transport is injectable (``post=``) so tests exercise parsing, budget
    and accounting without a network.

The API key is read from the ``api_key`` argument or ``ANTHROPIC_API_KEY``;
it is never logged, never written, never included in results.
"""
from __future__ import annotations

import ast
import dataclasses
import json
import os
import re
import time
import urllib.error
import urllib.request
from typing import Callable, Dict, List, Optional

from .corrupt import _LineIndex
from .tasks import Task

API_URL = "https://api.anthropic.com/v1/messages"
API_VERSION = "2023-06-01"

#: USD per million tokens (input, output). Estimates keyed by model-id substring,
#: longest match wins; unknown models fall back to the sonnet row and are
#: labelled as such in the usage summary. Prices change; this is an ESTIMATE.
PRICING_USD_PER_M: Dict[str, tuple] = {
    "claude-opus-4": (15.0, 75.0),
    "claude-sonnet-4": (3.0, 15.0),
    "claude-haiku-4": (1.0, 5.0),
    "claude-3-5-haiku": (0.8, 4.0),
}


@dataclasses.dataclass
class RepairResult:
    output: str
    meta: Dict = dataclasses.field(default_factory=dict)


class Repairer:
    name = "base"

    def repair(self, task: Task) -> RepairResult:  # pragma: no cover - interface
        raise NotImplementedError

    def usage_summary(self) -> Dict:
        return {}


# --------------------------------------------------------------------------- oracle

class OracleRepairer(Repairer):
    name = "oracle"

    def repair(self, task: Task) -> RepairResult:
        return RepairResult(task.corruption.invert(task.corrupted), {"inverse_of": task.corruption.id})


# --------------------------------------------------------------------------- sloppy

_BUILTIN_SAFE = {"self", "cls"}


class _LocalRenamer(ast.NodeTransformer):
    """Rename names that are ASSIGNED inside the function (locals), never
    parameters, globals, nonlocals, attributes or anything read-only."""

    def __init__(self, fn: ast.AST):
        params = {a.arg for a in fn.args.args + fn.args.kwonlyargs + fn.args.posonlyargs}
        if fn.args.vararg:
            params.add(fn.args.vararg.arg)
        if fn.args.kwarg:
            params.add(fn.args.kwarg.arg)
        declared = set()
        stored = set()
        for node in ast.walk(fn):
            if isinstance(node, (ast.Global, ast.Nonlocal)):
                declared.update(node.names)
            elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
                stored.add(node.id)
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and node is not fn:
                declared.add(node.name)  # nested defs keep their names
        self.map = {n: f"{n}_v2" for n in sorted(stored - params - declared - _BUILTIN_SAFE)
                    if not n.startswith("__")}

    def visit_Name(self, node):
        if node.id in self.map:
            return ast.copy_location(ast.Name(id=self.map[node.id], ctx=node.ctx), node)
        return node


class SloppyRepairer(Repairer):
    name = "sloppy"

    def rewrite_function(self, reference: str, func_lineno: int) -> str:
        tree = ast.parse(reference)
        fn = None
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.lineno == func_lineno:
                fn = node
                break
        if fn is None:
            raise ValueError(f"no function starts at line {func_lineno}")
        idx = _LineIndex(reference)
        first_line = min([fn.lineno] + [d.lineno for d in fn.decorator_list])
        start = idx.starts[first_line - 1] + fn.col_offset
        end = idx.offset(fn.end_lineno, fn.end_col_offset)
        # 1. rename locals  2. wrap body in a transparent try/except  3. unparse
        renamed = _LocalRenamer(fn).visit(fn)
        body = renamed.body
        doc = []
        if body and isinstance(body[0], ast.Expr) and isinstance(getattr(body[0], "value", None), ast.Constant) \
                and isinstance(body[0].value.value, str):
            doc, body = body[:1], body[1:]
        handler = ast.ExceptHandler(type=ast.Name(id="Exception", ctx=ast.Load()), name=None,
                                    body=[ast.Raise(exc=None, cause=None)])
        wrapped = ast.Try(body=body or [ast.Pass()], handlers=[handler], orelse=[], finalbody=[])
        renamed.body = doc + [wrapped]
        ast.fix_missing_locations(renamed)
        text = ast.unparse(renamed)
        indent = " " * fn.col_offset
        text = "\n".join((indent + ln if ln.strip() else ln) for ln in text.splitlines())
        text = text[len(indent):]  # first line already sits at col_offset
        return reference[:start] + text + reference[end:]

    def repair(self, task: Task) -> RepairResult:
        out = self.rewrite_function(task.reference, task.corruption.func_lineno)
        return RepairResult(out, {"rewrote": task.corruption.func})


# --------------------------------------------------------------------------- anthropic

_FENCE_RE = re.compile(r"```(?:python|py)?[ \t]*\r?\n(.*?)\r?\n```", re.S)

SYSTEM_PROMPT = (
    "You are repairing a bug in one Python module of an existing codebase. "
    "Return the COMPLETE corrected module inside a single ```python fenced block "
    "and nothing else: no prose before or after the block."
)

PRESERVATION_INSTRUCTION = (
    "Make the MINIMAL edit that fixes the bug. Preserve the original implementation "
    "exactly: keep every line that is not the bug byte-for-byte, including comments, "
    "blank lines, quoting, formatting, variable names and structure. Do not refactor, "
    "reformat, rename, reorder, simplify or add defensive code."
)


def build_prompt(task: Task, preservation: bool) -> str:
    parts = [
        f"File: {task.source_file}",
        f"The test target `{task.test_target}` fails against this file. "
        f"The bug is inside the function `{task.corruption.func}`. Fix it.",
    ]
    if preservation:
        parts.append(PRESERVATION_INSTRUCTION)
    parts.append("pytest output (tail):\n```\n" + task.fail_tail[-3000:] + "\n```")
    parts.append("Current file contents:\n```python\n" + task.corrupted + "\n```")
    return "\n\n".join(parts)


def extract_code(text: str) -> str:
    """The LARGEST fenced block. Models routinely disobey "nothing else" and put a
    short diagnostic snippet in a first fence before the whole file in a second;
    taking the first fence scored such a repair as a 47-byte file (measured)."""
    blocks = [m.group(1) for m in _FENCE_RE.finditer(text)]
    if blocks:
        return max(blocks, key=len) + "\n"
    return text


def estimate_cost_usd(model: str, input_tokens: int, output_tokens: int) -> tuple:
    key = ""
    for k in PRICING_USD_PER_M:
        if k in model and len(k) > len(key):
            key = k
    known = bool(key)
    pin, pout = PRICING_USD_PER_M[key] if known else PRICING_USD_PER_M["claude-sonnet-4"]
    return (input_tokens * pin + output_tokens * pout) / 1e6, known


def urllib_post(url: str, headers: Dict[str, str], body: bytes, timeout: int = 180) -> tuple:
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")


class BudgetExceeded(RuntimeError):
    pass


class AnthropicRepairer(Repairer):
    def __init__(self, preservation: bool, model: str, api_key: Optional[str] = None,
                 max_output_tokens: int = 4096, max_calls: int = 50, max_cost_usd: float = 2.0,
                 post: Optional[Callable] = None, temperature: float = 0.0, retries: int = 3,
                 shared_usage: Optional[Dict] = None):
        self.preservation = preservation
        self.name = "anthropic_preserve" if preservation else "anthropic_plain"
        self.model = model
        self._key = api_key or os.environ.get("ANTHROPIC_API_KEY", "")
        self.max_output_tokens = max_output_tokens
        self.max_calls = max_calls
        self.max_cost_usd = max_cost_usd
        self.post = post or urllib_post
        self.temperature = temperature
        self.retries = retries
        # usage may be SHARED between the two arms so the cap is on total spend
        self.usage = shared_usage if shared_usage is not None else {"calls": 0, "input_tokens": 0, "output_tokens": 0}
        self.calls_here = 0

    # -- accounting
    def usage_summary(self) -> Dict:
        cost, known = estimate_cost_usd(self.model, self.usage["input_tokens"], self.usage["output_tokens"])
        return {"model": self.model, **self.usage, "est_cost_usd": round(cost, 4),
                "pricing_known": known, "calls_this_arm": self.calls_here}

    def _check_budget(self) -> None:
        if self.calls_here >= self.max_calls:
            raise BudgetExceeded(f"{self.name}: call cap {self.max_calls} reached")
        cost, _ = estimate_cost_usd(self.model, self.usage["input_tokens"], self.usage["output_tokens"])
        if cost >= self.max_cost_usd:
            raise BudgetExceeded(f"cumulative estimated cost ${cost:.3f} >= cap ${self.max_cost_usd}")

    # -- the call
    def _call(self, prompt: str) -> Dict:
        if not self._key:
            raise RuntimeError("no API key: pass api_key= or set ANTHROPIC_API_KEY")
        body = json.dumps({
            "model": self.model,
            "max_tokens": self.max_output_tokens,
            "temperature": self.temperature,
            "system": SYSTEM_PROMPT,
            "messages": [{"role": "user", "content": prompt}],
        }).encode("utf-8")
        headers = {"x-api-key": self._key, "anthropic-version": API_VERSION,
                   "content-type": "application/json"}
        last = None
        for attempt in range(self.retries):
            status, text = self.post(API_URL, headers, body)
            if status == 200:
                return json.loads(text)
            last = (status, text[:300])
            if status in (408, 409, 429, 500, 502, 503, 504, 529):
                time.sleep(2 ** attempt * 3)
                continue
            break
        raise RuntimeError(f"Anthropic API error {last[0]}: {last[1]}")

    def repair(self, task: Task) -> RepairResult:
        self._check_budget()
        prompt = build_prompt(task, self.preservation)
        t0 = time.time()
        resp = self._call(prompt)
        secs = round(time.time() - t0, 2)
        usage = resp.get("usage", {})
        self.usage["calls"] += 1
        self.calls_here += 1
        self.usage["input_tokens"] += int(usage.get("input_tokens", 0))
        self.usage["output_tokens"] += int(usage.get("output_tokens", 0))
        text = "".join(b.get("text", "") for b in resp.get("content", []) if b.get("type") == "text")
        code = extract_code(text)
        return RepairResult(code, {
            "model": resp.get("model", self.model),
            "stop_reason": resp.get("stop_reason"),
            "input_tokens": usage.get("input_tokens"),
            "output_tokens": usage.get("output_tokens"),
            "fenced": bool(_FENCE_RE.search(text)),
            "n_fences": len(_FENCE_RE.findall(text)),
            "raw_chars": len(text),
            "truncated": resp.get("stop_reason") == "max_tokens",
            "secs": secs,
            "preservation": self.preservation,
        })


def list_models(api_key: str, post: Optional[Callable] = None) -> List[str]:
    """GET /v1/models -- used by run_eval to fail loudly on a wrong model id."""
    req = urllib.request.Request("https://api.anthropic.com/v1/models?limit=100",
                                 headers={"x-api-key": api_key, "anthropic-version": API_VERSION})
    with urllib.request.urlopen(req, timeout=60) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    return [m["id"] for m in data.get("data", [])]
