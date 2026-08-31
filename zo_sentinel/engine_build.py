"""
engine_build.py -- the deterministic builder ENGINE, made a first-class citizen.

WHY (the 2026-07-01 build_provenance finding): on the hard /app tail the goose
AGENTIC harness ghosts ~76% ("output_file not produced") while the deterministic
single-shot builder clears the same directives -- the differentiator is the
ENGINE, not the model tier. But the existing engine path
(goose_runner.call_minimax_fallback) was systematically STARVED relative to the
goose path:

  goose gets:   task content + graph context + prior-failure lessons +
                data-access/schema grounding + per-attempt ladder routing.
  fallback got: "Complete this task: <raw content>" -- one bare shot, one rung,
                no lessons, no schema grounding, no repair -- then had to clear
                the SAME gates (py_compile, schema-PRM, __main__ self-test).

So "both engines failed" ghosts were often just the second engine failing for
lack of the context the first engine already had in hand. This module fixes the
engine, not the symptoms:

  1. GROUNDING PARITY -- build_with_engine() takes the SAME fully-composed task
     text the goose path uses (content + graph + lessons + data-access).
     CORRECTION 2026-08-11: for six weeks this line was read as meaning the
     engine received SCHEMA grounding. It did not. The composed task carried
     table and class NAMES plus a pointer to docs/SCHEMA_TRUTH.md -- which a
     single chat completion with no filesystem cannot open. Real COLUMN lists
     reached only the 8/day SOA canary. goose_runner._engine_task now inlines
     them here too; see [engine-ground] in the log for the arming witness.
  2. RUNG ESCALATION BY ATTEMPT -- ghost retry N runs a higher capable rung
     (env-tunable ZO_ENGINE_RUNGS; capped at free rungs -- escalation.py's cost
     gate remains the paid backstop).
  3. ONE BOUNDED REPAIR ROUND -- after writing the file it runs the local
     checks (py_compile + __main__ self-test in the same sqlite/no-Clerk env the
     authoritative gate uses); on failure it feeds the ACTUAL error back to the
     rung once and rewrites. Deterministic engines can repair; agentic loops
     just churn.

NON-NEGOTIABLE SAFETY PROPERTIES:
  - This module NEVER completes a directive. It only (atomically) writes the
    declared output file; goose_runner's authoritative gate chain
    (output_confirmed + syntax + schema-PRM + self-test) still decides
    completion, exactly as before.
  - Idempotent: re-runs rewrite the same declared output atomically
    (tmp + os.replace); no other filesystem side effects; no state files.
  - Fail-open: any exception returns {"success": False} and goose_runner falls
    back to the legacy call_minimax_fallback path unchanged.
  - Flag-gated, default OFF: env ZO_ENGINE_BUILD=1 or sentinel file
    directives/.engine_build_on containing "1" (read fresh each call -- flip
    live, no restart; "0" disables).

Pure stdlib + requests + the pure build_completion helpers. No import-time side
effects. Unit-testable with an injected `post` callable (no network).
"""
from __future__ import annotations

import os
import py_compile
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Callable, Optional

import requests

from zo_sentinel.build_completion import declared_output

SHIM_URL = "http://127.0.0.1:8796/v1/chat/completions"
SENTINEL_NAME = ".engine_build_on"
ENV_FLAG = "ZO_ENGINE_BUILD"
# Capable-rung escalation for the ENGINE (attempt-indexed, clamped to last).
# zo-ladder-nvidia = the proven capable coder rung; zo-ladder-high = top FREE
# escalation rung. Deliberately NO paid rung here -- cost ceiling discipline.
DEFAULT_RUNGS = ("zo-ladder-nvidia", "zo-ladder-high")
MAX_REPAIRS = int(os.environ.get("ZO_ENGINE_MAX_REPAIRS", "1"))
SELFTEST_TIMEOUT = int(os.environ.get("ZO_ENGINE_SELFTEST_TIMEOUT", "90"))


def enabled(directives_root) -> bool:
    """Gate resolved through the declarative policy layer (zo_sentinel.policy:
    env > durable override > legacy sentinel > policy_defaults.toml), read
    fresh each call. Fail-open FALLBACK: the original inline env+sentinel
    logic, so a policy-layer fault degrades to prior behavior."""
    try:
        from zo_sentinel import policy
        return policy.flag("builder.engine_build",
                           directives_root=directives_root)
    except Exception:
        pass
    val = os.environ.get(ENV_FLAG, "")
    if val.strip().lower() not in ("", "0", "off", "false"):
        return True
    try:
        sf = Path(directives_root) / SENTINEL_NAME
        return (sf.is_file()
                and sf.read_text(encoding="utf-8").strip().lower()
                not in ("", "0", "off", "false"))
    except Exception:
        return False


def _rungs() -> tuple:
    # env keeps highest precedence (policy resolves it too, but the inline
    # read stays as the fail-open fallback path).
    raw = os.environ.get("ZO_ENGINE_RUNGS", "")
    if not raw.strip():
        try:
            from zo_sentinel import policy
            raw = str(policy.value("builder.engine_rungs"))
        except Exception:
            raw = ""
    if raw.strip():
        parts = tuple(p.strip() for p in raw.split(",") if p.strip())
        if parts:
            return parts
    return DEFAULT_RUNGS


def _max_repairs() -> int:
    try:
        from zo_sentinel import policy
        return int(policy.value("builder.engine_max_repairs"))
    except Exception:
        return MAX_REPAIRS


def rung_for_attempt(attempt: int) -> str:
    """Attempt-indexed capable rung, clamped to the ladder top (free rungs only)."""
    rungs = _rungs()
    return rungs[min(max(attempt, 0), len(rungs) - 1)]


def strip_code_fences(txt: str) -> str:
    """Same asymmetric-fence-safe extraction goose_runner uses (kept in sync):
    prefer the FIRST well-formed fenced block; otherwise drop stray fence lines."""
    import re
    s = (txt or "").strip()
    m = re.search(r"```[^\n]*\n(.*?)```", s, re.DOTALL)
    if m:
        return m.group(1).strip()
    lines = [ln for ln in s.splitlines() if not ln.lstrip().startswith("```")]
    return chr(10).join(lines).strip()


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".engine.tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        os.replace(tmp, str(path))
    finally:
        try:
            if os.path.exists(tmp):
                os.unlink(tmp)
        except OSError:
            pass


def _local_check(out: Path) -> tuple:
    """(ok, error_detail). MIRRORS the authoritative gates' semantics for the
    repair loop only -- goose_runner's real gates still decide completion:
    py_compile for .py, then the __main__ self-test (sqlite/no-Clerk env, PASS
    required) when the file declares one. Environment/import errors degrade to
    ok=True, same as _selftest_gate, so we never repair-loop on missing deps."""
    try:
        src = out.read_text(encoding="utf-8", errors="ignore")
    except Exception as e:
        return False, f"unreadable output: {e}"
    if out.suffix == ".py":
        try:
            py_compile.compile(str(out), doraise=True)
        except Exception as e:
            return False, f"py_compile failed: {e}"
        if "__main__" in src:
            try:
                env = {**os.environ, "DATABASE_URL": "sqlite://",
                       "CLERK_PUBLISHABLE_KEY": ""}
                proc = subprocess.run([sys.executable, str(out)],
                                      capture_output=True, text=True,
                                      timeout=SELFTEST_TIMEOUT, env=env)
                combined = (proc.stdout or "") + (proc.stderr or "")
                if proc.returncode == 0 and "PASS" in combined:
                    return True, ""
                if ("ModuleNotFoundError" in combined
                        or "ImportError" in combined):
                    return True, ""   # environment gap, not code failure
                return False, ("self-test did not PASS "
                               f"(rc={proc.returncode}): {combined[-1200:]}")
            except subprocess.TimeoutExpired:
                return False, f"self-test timed out after {SELFTEST_TIMEOUT}s"
            except Exception:
                return True, ""       # can't run it here -> defer to real gate
    return True, ""


def _chat(post: Callable, shim_url: str, model: str, system: str, user: str,
          timeout: int) -> str:
    resp = post(shim_url, json={
        "model": model,
        "messages": [{"role": "system", "content": system},
                     {"role": "user", "content": user}],
        "temperature": 0.2,
        "max_tokens": 16384,
    }, timeout=timeout)
    if getattr(resp, "status_code", 0) != 200:
        raise RuntimeError(f"shim {getattr(resp, 'status_code', '?')}")
    return (resp.json().get("choices", [{}])[0]
            .get("message", {}).get("content", "")) or ""


_SYSTEM = (
    "You are the deterministic builder engine for zo-sentinel. Output ONLY the "
    "complete, final content of the requested file -- no commentary, no "
    "placeholders, no TODOs, no stub data. The file must be complete and "
    "self-contained."
)


def build_with_engine(directive: dict, task_text: str, attempt: int = 0,
                      home: Optional[str] = None,
                      shim_url: str = SHIM_URL,
                      timeout: int = 600,
                      post: Callable = requests.post,
                      log: Callable = print) -> dict:
    """One grounded engine build (+ up to MAX_REPAIRS repair rounds).

    Returns {"success", "result", "model", "repairs", "error"?}. NEVER raises.
    Writes ONLY the directive's declared output (atomic). Completion authority
    stays with goose_runner's gate chain.
    """
    model = rung_for_attempt(attempt)
    res = {"success": False, "result": "", "model": model, "repairs": 0,
           "fallback": "engine_build"}
    try:
        out = (declared_output(directive, home) if home is not None
               else declared_output(directive))
        acceptance = (
            f"\n\nWrite the COMPLETE content of `{out.name}`. It must compile "
            f"(py_compile) and its __main__ self-test must print PASS, exactly "
            f"as the directive's ACCEPTANCE describes." if out is not None else ""
        )
        _user = f"Complete this task: {task_text}{acceptance}"
        # ARMING WITNESS (CofC 2026-08-11 cond.5): asserted against the string
        # actually SENT, not against the composer that built it. A verify that
        # reads the caller's variable witnesses the code, not the request.
        log(f"[engine-ground] {out.name if out is not None else '?'}: "
            f"schema_in_sent_prompt={'REAL SCHEMA --' in _user} "
            f"prompt_chars={len(_user)}")
        txt = _chat(post, shim_url, model, _SYSTEM, _user, timeout)
        res["result"] = txt
        if out is None:
            # Edit-class directive: no single declared file to write/verify --
            # parity with the legacy fallback (trust process; real gates decide).
            res["success"] = bool(txt)
            return res
        code = strip_code_fences(txt)
        if not code.strip():
            res["error"] = "empty generation"
            return res
        _atomic_write(out, code)
        log(f"[engine] {model} wrote {len(code)} bytes -> {out.name}")
        ok, detail = _local_check(out)
        repairs = 0
        max_repairs = _max_repairs()
        while not ok and repairs < max_repairs:
            repairs += 1
            log(f"[engine] repair {repairs}/{max_repairs} for {out.name}: "
                f"{detail[:160]}")
            fix_prompt = (
                f"The file `{out.name}` you produced FAILED verification.\n"
                f"FAILURE:\n{detail[-1500:]}\n\n"
                f"Original task: {task_text[:4000]}\n\n"
                f"Output the corrected COMPLETE file content -- the whole file, "
                f"not a diff. It must compile and its __main__ self-test must "
                f"print PASS."
            )
            txt = _chat(post, shim_url, model, _SYSTEM, fix_prompt, timeout)
            code = strip_code_fences(txt)
            if not code.strip():
                break
            _atomic_write(out, code)
            ok, detail = _local_check(out)
        res["repairs"] = repairs
        res["success"] = ok
        if not ok:
            res["error"] = detail[:500]
        return res
    except Exception as e:
        res["error"] = f"{type(e).__name__}: {e}"
        return res
