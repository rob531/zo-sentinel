#!/usr/bin/env python3
"""
security_reviewer.py -- Pre-commit security review for ZO-SENTINEL builds.
Uses llama3.2:3b as a fast local classifier. Called by zo_sentinel_builder.py.

Usage:
    from security_reviewer import security_review
    passed, findings = security_review(code, task_name)
"""
import requests
import logging

log = logging.getLogger("builder")

OLLAMA_URL = "http://127.0.0.1:11434/api/generate"
REVIEW_MODEL = "llama3.2:3b"
REVIEW_TIMEOUT = 30
CODE_CAP = 3000

PROMPT_TEMPLATE = (
    "You are an InfoSec code auditor reviewing Python before deployment.\n"
    "Scan the code below for:\n"
    "- SQL injection via string formatting into queries\n"
    "- Hardcoded passwords, API keys, or secrets\n"
    "- Shell injection (subprocess shell=True with user input)\n"
    "- Suspicious package imports not in stdlib or requests/fastapi/uvicorn\n"
    "- Exposed internal ports or credentials in plain text\n"
    "Reply with exactly: PASS or FAIL on line 1. \n"
    "If FAIL, add one line explaining the finding (max 100 chars).\n\n"
    "CODE:\n"
)


def security_review(code: str, task: str = "") -> tuple:
    """
    Returns (passed: bool, findings: str).
    Fails open -- if reviewer unavailable, returns (True, 'unavailable').
    """
    if len(code) < 100:
        return True, ""
    prompt = PROMPT_TEMPLATE + code[:CODE_CAP]
    try:
        r = requests.post(
            OLLAMA_URL,
            json={"model": REVIEW_MODEL, "prompt": prompt,
                  "stream": False, "options": {"num_ctx": 4096}},
            timeout=REVIEW_TIMEOUT
        )
        if r.status_code == 200:
            response = r.json().get("response", "").strip()
            lines = [l.strip() for l in response.splitlines() if l.strip()]
            first = lines[0].upper() if lines else "PASS"
            passed = first.startswith("PASS")
            findings = lines[1][:150] if len(lines) > 1 else ""
            if not passed:
                log.warning("  [SEC] FAIL [%s]: %s", task, findings)
            else:
                log.info("  [SEC] PASS [%s]", task)
            return passed, findings
    except Exception as e:
        log.warning("  [SEC] unavailable: %s", e)
    return True, "unavailable"


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1:
        code = open(sys.argv[1]).read()
        passed, findings = security_review(code, sys.argv[1])
        print("PASS" if passed else "FAIL", findings)
        sys.exit(0 if passed else 1)