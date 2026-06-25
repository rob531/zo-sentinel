#!/usr/bin/env python3
"""
codeql_autofix.py  --  fetch open CodeQL alerts and open one fix-PR per file.

Called by .github/workflows/codeql-autofix.yml after a successful CodeQL scan on main.

Required env:
  GH_TOKEN           GitHub token (security-events:read, contents:write, pull-requests:write)
  ANTHROPIC_API_KEY  Anthropic API key
  GITHUB_REPOSITORY  owner/repo  (set automatically in Actions)
  GITHUB_SHA         current main HEAD SHA  (set automatically in Actions)

Optional env:
  CODEQL_AUTOFIX_MAX_FILES   max files to fix per run  (default 5, cost cap)
  CODEQL_AUTOFIX_SEVERITIES  comma-separated list      (default "critical,high")
"""
from __future__ import annotations

import base64
import json
import os
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from pathlib import Path

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
REPO = os.environ["GITHUB_REPOSITORY"]
SHA = os.environ["GITHUB_SHA"]
GH_TOKEN = os.environ["GH_TOKEN"]
ANTHROPIC_KEY = os.environ["ANTHROPIC_API_KEY"]
MAX_FILES = int(os.environ.get("CODEQL_AUTOFIX_MAX_FILES", "5"))
SEVERITIES = set(os.environ.get("CODEQL_AUTOFIX_SEVERITIES", "critical,high").split(","))
LABEL_FIX = "codeql-fix"
LABEL_AUTO = "autonomous-build"
BRANCH_PREFIX = "codeql-fix"


# ---------------------------------------------------------------------------
# GitHub helper
# ---------------------------------------------------------------------------
def gh(method: str, path: str, body=None, accept: str = "application/vnd.github+json"):
    url = f"https://api.github.com{path}"
    data = json.dumps(body).encode() if body else None
    req = urllib.request.Request(url, data=data, method=method, headers={
        "Authorization": f"Bearer {GH_TOKEN}",
        "Accept": accept,
        "X-GitHub-Api-Version": "2022-11-28",
        "Content-Type": "application/json",
    })
    try:
        with urllib.request.urlopen(req) as r:
            return json.loads(r.read()), r.status
    except urllib.error.HTTPError as e:
        body_txt = e.read().decode()
        print(f"  GH {method} {path} -> HTTP {e.code}: {body_txt[:200]}", file=sys.stderr)
        return None, e.code


def gh_get_all(path: str) -> list:
    """Paginate through all results."""
    results = []
    url = f"https://api.github.com{path}"
    while url:
        req = urllib.request.Request(url, headers={
            "Authorization": f"Bearer {GH_TOKEN}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        })
        try:
            with urllib.request.urlopen(req) as r:
                results.extend(json.loads(r.read()))
                link = r.headers.get("Link", "")
                url = None
                for part in link.split(","):
                    if 'rel="next"' in part:
                        url = part.split(";")[0].strip().strip("<>")
                        break
        except urllib.error.HTTPError:
            break
    return results


# ---------------------------------------------------------------------------
# Anthropic helper
# ---------------------------------------------------------------------------
def claude_fix(file_path: str, content: str, alerts: list[dict]) -> str | None:
    alert_lines = "\n".join(
        f"- Line {a['most_recent_instance']['location']['start_line']}: "
        f"[{a['rule']['id']}] {a['most_recent_instance']['message']['text']}"
        for a in alerts
    )
    prompt = (
        f"Fix the following CodeQL security alerts in this Python file.\n"
        f"Return ONLY the complete corrected file content with no explanation, "
        f"no markdown fences, no preamble.\n\n"
        f"File: {file_path}\n\n"
        f"Alerts to fix:\n{alert_lines}\n\n"
        f"Original file:\n{content}"
    )
    body = {
        "model": "claude-haiku-4-5-20251001",
        "max_tokens": 8192,
        "messages": [{"role": "user", "content": prompt}],
    }
    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=json.dumps(body).encode(),
        method="POST",
        headers={
            "x-api-key": ANTHROPIC_KEY,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req) as r:
            resp = json.loads(r.read())
            text = resp["content"][0]["text"].strip()
            # Strip accidental markdown fences
            if text.startswith("```"):
                lines = text.splitlines()
                start = 1
                end = len(lines) - 1 if lines[-1].strip() == "```" else len(lines)
                text = "\n".join(lines[start:end])
            return text
    except urllib.error.HTTPError as e:
        print(f"  Anthropic error {e.code}: {e.read().decode()[:200]}", file=sys.stderr)
        return None
    except Exception as e:
        print(f"  Anthropic exception: {e}", file=sys.stderr)
        return None


# ---------------------------------------------------------------------------
# Syntax validation
# ---------------------------------------------------------------------------
def passes_ruff(content: str, path: str) -> bool:
    with tempfile.NamedTemporaryFile(suffix=".py", mode="w", delete=False) as f:
        f.write(content)
        tmp = f.name
    try:
        r = subprocess.run(
            ["ruff", "check", "--select", "E9,F", "--quiet", tmp],
            capture_output=True, timeout=15,
        )
        return r.returncode == 0
    except FileNotFoundError:
        # ruff not installed -- fall back to py_compile
        try:
            subprocess.run(
                [sys.executable, "-m", "py_compile", tmp],
                capture_output=True, timeout=10, check=True,
            )
            return True
        except subprocess.CalledProcessError:
            return False
    finally:
        Path(tmp).unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main() -> int:
    print(f"CodeQL autofix  repo={REPO}  sha={SHA[:12]}  max_files={MAX_FILES}  severities={SEVERITIES}")

    # 1. Fetch open alerts at the target severities
    all_alerts = gh_get_all(f"/repos/{REPO}/code-scanning/alerts?state=open&per_page=100")
    target = [
        a for a in all_alerts
        if a.get("rule", {}).get("severity") in SEVERITIES
        or a.get("rule", {}).get("security_severity_level") in SEVERITIES
    ]
    print(f"Open {'/'.join(sorted(SEVERITIES))} alerts: {len(target)} (total open: {len(all_alerts)})")
    if not target:
        print("Nothing to fix.")
        return 0

    # 2. Group by file
    by_file: dict[str, list] = {}
    for a in target:
        fp = a["most_recent_instance"]["location"]["path"]
        by_file.setdefault(fp, []).append(a)

    # 3. Find files already covered by an open codeql-fix PR (dedup)
    open_prs = gh_get_all(f"/repos/{REPO}/pulls?state=open&per_page=100")
    existing_fix_branches = {
        pr["head"]["ref"] for pr in open_prs
        if pr["head"]["ref"].startswith(BRANCH_PREFIX + "/")
    }

    fixed = 0
    for file_path, alerts in list(by_file.items())[:MAX_FILES]:
        safe_name = file_path.replace("/", "_").replace(".", "_")
        branch = f"{BRANCH_PREFIX}/{safe_name}"
        if branch in existing_fix_branches:
            print(f"  {file_path}: open fix-PR already exists, skipping")
            continue

        print(f"\n  Fixing {file_path} ({len(alerts)} alert(s))...")

        # Fetch file content from main
        file_data, status = gh("GET", f"/repos/{REPO}/contents/{file_path}?ref={SHA}")
        if not file_data or status != 200:
            print(f"    Cannot fetch file (status {status}), skipping")
            continue
        try:
            original = base64.b64decode(file_data["content"]).decode("utf-8")
            file_sha = file_data["sha"]
        except Exception as e:
            print(f"    Decode error: {e}, skipping")
            continue

        # Ask Claude for a fix
        fixed_content = claude_fix(file_path, original, alerts)
        if not fixed_content:
            print("    LLM returned nothing, skipping")
            continue
        if fixed_content == original:
            print("    LLM returned unchanged content, skipping")
            continue

        # Validate
        if not passes_ruff(fixed_content, file_path):
            print("    Fixed content fails ruff/py_compile, skipping (unsafe fix)")
            continue

        # Create branch from main
        _, br_status = gh("POST", f"/repos/{REPO}/git/refs", {
            "ref": f"refs/heads/{branch}", "sha": SHA
        })
        if br_status not in (200, 201):
            print(f"    Branch creation failed (status {br_status}), skipping")
            continue

        # Commit fix
        alert_summary = "; ".join(
            f"line {a['most_recent_instance']['location']['start_line']} [{a['rule']['id']}]"
            for a in alerts[:3]
        )
        if len(alerts) > 3:
            alert_summary += f" + {len(alerts)-3} more"
        commit_body = {
            "message": f"fix(codeql): {file_path} — {alert_summary}",
            "content": base64.b64encode(fixed_content.encode()).decode(),
            "sha": file_sha,
            "branch": branch,
        }
        _, commit_status = gh("PUT", f"/repos/{REPO}/contents/{file_path}", commit_body)
        if commit_status not in (200, 201):
            print(f"    Commit failed (status {commit_status}), skipping")
            continue

        # Open PR
        rule_ids = sorted({a["rule"]["id"] for a in alerts})
        pr_body = (
            f"## CodeQL autofix\n\n"
            f"Automated fix for {len(alerts)} open CodeQL alert(s) in `{file_path}`.\n\n"
            f"**Rules**: {', '.join(f'`{r}`' for r in rule_ids)}\n\n"
            f"**Alerts fixed**:\n"
            + "\n".join(
                f"- Line {a['most_recent_instance']['location']['start_line']}: "
                f"[{a['rule']['id']}] {a['most_recent_instance']['message']['text'][:120]}"
                for a in alerts
            )
            + f"\n\n**Severity**: {', '.join(sorted(SEVERITIES))}\n\n"
            f"Generated by `tools/codeql_autofix.py` via Claude Haiku. "
            f"Standard gates (ruff, smoke-ladder, frontend) apply before merge."
        )
        pr_data, pr_status = gh("POST", f"/repos/{REPO}/pulls", {
            "title": f"fix(codeql): {file_path} ({len(alerts)} alert{'s' if len(alerts)>1 else ''})",
            "head": branch,
            "base": "main",
            "body": pr_body,
        })
        if pr_status not in (200, 201) or not pr_data:
            print(f"    PR creation failed (status {pr_status}), skipping")
            continue

        # Label the PR so it flows through auto-merge
        gh("POST", f"/repos/{REPO}/issues/{pr_data['number']}/labels",
           [LABEL_FIX, LABEL_AUTO])

        print(f"    Opened PR #{pr_data['number']}: {pr_data['html_url']}")
        fixed += 1

    print(f"\nDone. Fix PRs opened: {fixed}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
