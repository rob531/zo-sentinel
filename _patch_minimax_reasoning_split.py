#!/usr/bin/env python3
"""Apply reasoning_split patch to two files. One-shot.

Replaces the existing call_minimax bodies with versions that:
  1. Pass reasoning_split=True in the request JSON
  2. Log presence/length of reasoning_details for telemetry
  3. Keep the stripper as fallback (defensive)

Idempotent: detects already-patched files and skips.
Writes .bak.<ts> backups before editing.
"""
import pathlib, datetime, sys

TS = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')

DIRECTIVE_GEN = pathlib.Path('/home/workspace/zo_sentinel/sentinel_directive_generator.py')
BUILDER = pathlib.Path('/home/workspace/zo_mesh/zo_sentinel_builder.py')

MARKER = '# 2026-04-21: reasoning_split=True separates'

# ---- directive_gen patch ----

DG_OLD = '''    r = post_with_retry(
        "https://api.minimax.io/v1/chat/completions",
        headers={"Authorization": f"Bearer {api_key}",
                 "Content-Type": "application/json"},
        json={"model": "MiniMax-M2.7",
              "messages": [{"role": "user", "content": prompt}],
              "max_tokens": 4096},
        retries=MINIMAX_RETRIES,
        backoff=MINIMAX_BACKOFF,
        timeout=MINIMAX_TIMEOUT
    )
    if r is None:
        log.warning("MiniMax call failed: all %d retries exhausted",
                    MINIMAX_RETRIES)
        return ""
    if r.status_code != 200:
        log.warning("MiniMax HTTP %d: %s", r.status_code, r.text[:200])
        return ""
    try:
        choices = r.json().get("choices", [])
        if not choices:
            log.warning("MiniMax returned 200 with no choices")
            return ""
        return choices[0].get("message", {}).get("content", "").strip()
    except Exception as e:
        log.warning("MiniMax response parse error: %s", e)
        return ""'''

DG_NEW = '''    r = post_with_retry(
        "https://api.minimax.io/v1/chat/completions",
        headers={"Authorization": f"Bearer {api_key}",
                 "Content-Type": "application/json"},
        # 2026-04-21: reasoning_split=True separates the model's chain-of-thought
        # into a dedicated reasoning_details field instead of embedding it in
        # content via <think>...</think> tags. Removes 40-70% of bytes per
        # response and removes the unclosed-tag parse failure mode.
        # Confirmed against https://platform.minimax.io/docs/api-reference/text-openai-api
        # The stripper in generate_directives() is kept as belt-and-suspenders
        # in case MiniMax silently changes/removes the parameter again.
        json={"model": "MiniMax-M2.7",
              "messages": [{"role": "user", "content": prompt}],
              "max_tokens": 4096,
              "reasoning_split": True},
        retries=MINIMAX_RETRIES,
        backoff=MINIMAX_BACKOFF,
        timeout=MINIMAX_TIMEOUT
    )
    if r is None:
        log.warning("MiniMax call failed: all %d retries exhausted",
                    MINIMAX_RETRIES)
        return ""
    if r.status_code != 200:
        log.warning("MiniMax HTTP %d: %s", r.status_code, r.text[:200])
        return ""
    try:
        choices = r.json().get("choices", [])
        if not choices:
            log.warning("MiniMax returned 200 with no choices")
            return ""
        msg = choices[0].get("message", {})
        content = msg.get("content", "").strip()
        # Telemetry: log whether reasoning_split was honored
        rd = msg.get("reasoning_details")
        if rd:
            try:
                rd_chars = sum(len(x.get("text", "")) for x in rd if isinstance(x, dict))
                log.info("MiniMax reasoning_split honored: content=%db reasoning=%db",
                         len(content), rd_chars)
            except Exception:
                log.info("MiniMax reasoning_split honored (reasoning_details present)")
        else:
            log.info("MiniMax reasoning_split NOT honored: content=%db (stripper will run)",
                     len(content))
        return content
    except Exception as e:
        log.warning("MiniMax response parse error: %s", e)
        return ""'''

# ---- builder patch ----

BL_OLD = '''def minimax_generate(prompt: str, model: str = "MiniMax-M2.7") -> str:
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
                  "max_tokens": 8192},
            timeout=120
        )
        if r.status_code == 200:
            choices = r.json().get("choices", [])
            raw  = choices[0].get("message", {}).get("content", "").strip() if choices else ""
            text = minimax_strip_think(raw)
            log.info("  minimax: raw=%db stripped=%db", len(raw), len(text))
            valid, reason = content_is_valid(text)
            if valid: return text
            log.warning("  minimax: %s", reason)
        else:
            log.warning("  minimax: HTTP %s %s", r.status_code, r.text[:80])
    except Exception as e:
        log.warning("  minimax: %s", e)
    return ""'''

BL_NEW = '''def minimax_generate(prompt: str, model: str = "MiniMax-M2.7") -> str:
    api_key = os.environ.get("MINIMAX_API_KEY", "")
    if not api_key:
        log.warning("  minimax: MINIMAX_API_KEY not set")
        return ""
    try:
        r = requests.post(
            "https://api.minimax.io/v1/chat/completions",
            headers={"Authorization": "Bearer " + api_key,
                     "Content-Type": "application/json"},
            # 2026-04-21: reasoning_split=True separates the model's chain-of-thought
            # into a dedicated reasoning_details field instead of embedding it in
            # content via <think>...</think> tags. Removes 40-70% of bytes per
            # response and removes the unclosed-tag parse failure mode.
            # Confirmed against https://platform.minimax.io/docs/api-reference/text-openai-api
            # minimax_strip_think (back-compat wrapper around _strip_reasoning_preamble)
            # is kept as belt-and-suspenders in case the parameter is silently changed.
            json={"model": model,
                  "messages": [{"role": "user", "content": prompt}],
                  "max_tokens": 8192,
                  "reasoning_split": True},
            timeout=120
        )
        if r.status_code == 200:
            choices = r.json().get("choices", [])
            msg  = choices[0].get("message", {}) if choices else {}
            raw  = msg.get("content", "").strip()
            # Telemetry: log whether reasoning_split was honored
            rd = msg.get("reasoning_details")
            if rd:
                try:
                    rd_chars = sum(len(x.get("text", "")) for x in rd if isinstance(x, dict))
                    log.info("  minimax: reasoning_split honored content=%db reasoning=%db",
                             len(raw), rd_chars)
                except Exception:
                    log.info("  minimax: reasoning_split honored (reasoning_details present)")
            text = minimax_strip_think(raw)
            log.info("  minimax: raw=%db stripped=%db", len(raw), len(text))
            valid, reason = content_is_valid(text)
            if valid: return text
            log.warning("  minimax: %s", reason)
        else:
            log.warning("  minimax: HTTP %s %s", r.status_code, r.text[:80])
    except Exception as e:
        log.warning("  minimax: %s", e)
    return ""'''


def patch_file(path: pathlib.Path, old: str, new: str, label: str) -> bool:
    src = path.read_text()
    if MARKER in src:
        print(f"[{label}] SKIP: already patched (marker found)")
        return False
    if old not in src:
        print(f"[{label}] FAIL: old block not found in {path}")
        return False
    bak = path.with_suffix(path.suffix + f".bak.{TS}")
    bak.write_text(src)
    new_src = src.replace(old, new, 1)
    path.write_text(new_src)
    print(f"[{label}] OK: patched {path}")
    print(f"  backup: {bak}")
    print(f"  size: {len(src)} -> {len(new_src)} bytes")
    return True


def main():
    print("=== reasoning_split patch ===")
    print(f"timestamp: {TS}")
    print()
    ok_dg = patch_file(DIRECTIVE_GEN, DG_OLD, DG_NEW, "directive_gen")
    print()
    ok_bl = patch_file(BUILDER, BL_OLD, BL_NEW, "builder")
    print()
    print("=== syntax check ===")
    import ast
    for label, p in [("directive_gen", DIRECTIVE_GEN), ("builder", BUILDER)]:
        try:
            ast.parse(p.read_text())
            print(f"[{label}] AST OK")
        except SyntaxError as e:
            print(f"[{label}] AST FAIL line {e.lineno}: {e.msg}")
            sys.exit(1)
    print()
    print("=== summary ===")
    print(f"directive_gen: {'patched' if ok_dg else 'unchanged'}")
    print(f"builder:       {'patched' if ok_bl else 'unchanged'}")
    print("Restart processes to pick up changes:")
    print("  pgrep -f 'python3 /home/workspace/zo_sentinel/sentinel_directive_generator\\.py$' | xargs -r kill")
    print("  pgrep -f 'python3 /home/workspace/zo_mesh/zo_sentinel_builder\\.py$' | xargs -r kill")


if __name__ == "__main__":
    main()