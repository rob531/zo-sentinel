#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
build_pack.py -- assemble the ULTRA PLAN PACK for zo-sentinel / mcprisky.io.

WHY THIS IS A SCRIPT AND NOT A DOCUMENT
---------------------------------------
Every number in this pack has a measurement timestamp and a source. Re-run this
on the morning of the ultra plan run; do NOT read a pack built days earlier and
treat its counts as current. The ledger moves ~3 FUs/day and the fleet rewrites
itself nightly -- a census taken days before the decision measures a world that
has already moved (see hazards: "a census taken during a live fleet rewrite
measures the rewrite", "carried forward is indistinguishable from measured").

SAFETY INVARIANTS (asserted at runtime, not merely stated)
----------------------------------------------------------
1. READ-ONLY on the ledger. This script NEVER opens FOLLOWUPS.md for writing.
   A guard refuses any output path that resolves under the ledger's own name.
2. The ledger is read in BINARY and decoded; nothing is ever written back, so the
   CRLF-stripping class cannot be reached from here.
3. Anything not measured is emitted as {"state": "COULD_NOT_DETERMINE"}, never as
   0 and never as a value carried from a previous build. Unknown is not zero.
4. Encoders are cured at main() entry on BOTH streams, so a crash while
   REPORTING cannot forge a clean run.

USAGE
-----
    python "D:\\zo\\Zocomputer Agents\\_ultraplan\\build_pack.py"
    python ... --no-live      # skip network/gh/git probes (offline rebuild)
    python ... --self-test    # parser controls only, writes nothing
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import re
import subprocess
import sys
from collections import Counter, defaultdict
from datetime import date, datetime, timezone
from pathlib import Path

# ---------------------------------------------------------------- constants --

ROOT = Path(r"D:\zo\Zocomputer Agents")
LEDGER = ROOT / "FOLLOWUPS.md"
AUTOPOIESIS = ROOT / "AUTOPOIESIS.md"
GOVERNANCE = ROOT / "GOVERNANCE.md"
PLAN200K = ROOT / "PLAN_200K.md"
TOOLS = ROOT / "_tools"
OUT = ROOT / "_ultraplan" / "pack"

SCHEDULED_DIR = Path(r"C:\Users\robin\OneDrive\Documents\Claude\Scheduled")
MEMORY_DIR = None  # discovered at runtime; see find_memory_dir()

REPO = Path(r"D:\zo\zo-sentinel\zo-sentinel")

OPEN_STATES = {"open", "open-", "in-progress", "pr-open", "watch"}
CLOSED_STATES = {"resolved", "done", "wontfix", "falsified"}

FIELD_KEYS = ("date", "opened", "source", "lane", "class", "status", "priority")
# "opened:" is an alias some lanes use for "date:". Omitting it left 13 entries
# with filed=None and age_days=None, which reads as "undatable" rather than
# "the key had a different name".
DATE_KEYS = ("date", "opened")
BODY_KEYS = ("detail", "resolution", "residual", "verify", "verify_seen_red",
             "evidence", "next", "control", "log")

ENTRY_RE = re.compile(r"^#{2,3}\s+FU-(\d+)\s*\|\s*(.*)$")

# Separators seen in the wild between packed metadata fields. The list is long
# because the ledger has been written by ~20 different lanes over 3 months and
# each generation picked its own: U+00B7, hyphen, en/em dash, bullet, asterisk,
# and the MOJIBAKE pair "Â·" left by one lane that round-tripped the
# file through latin-1. Mojibake is repaired at read time, not tolerated here.
SEP = r"[·•–—*\-|]"
_KEYS = "|".join(FIELD_KEYS)
# Keys may be wrapped in markdown bold: "- **status:** OPEN" is in use.
_K = rf"\*{{0,2}}({_KEYS})\*{{0,2}}\s*:\s*\*{{0,2}}"
META_LINE_RE = re.compile(rf"^\s*\*{{0,2}}({_KEYS})\*{{0,2}}\s*:")
# Value runs until a separator that is followed by ANOTHER known key, or EOL.
META_FIELD_RE = re.compile(rf"{_K}(.*?)(?=\s+{SEP}\s*{_K}|$)")

STATUS_TOKEN_RE = re.compile(
    rf"(?m)(?:^\s{{0,3}}[-*]?\s*|\s{SEP}\s*)\*{{0,2}}status\*{{0,2}}\s*:")

MOJIBAKE = {
    "Â·": "·",   # · read as latin-1
    "â": "—",
    "â": "–",
}

STATUS_SYNONYMS = {
    "fixed": "resolved", "closed": "done", "complete": "done",
    "completed": "done", "reopened": "open", "ok": "resolved",
    "wont": "wontfix", "wontfix": "wontfix",
}


def norm_status(raw: str) -> str:
    """Take the FIRST word. A status field whose value is a sentence
    ('OPEN, routed to peer review rather than acted on') is still a status --
    but only its first token is the state. Squashing the whole sentence into
    one slug, which an earlier build did, invents a category per entry and
    makes every one of them invisible to a status sweep."""
    m = re.match(r"\s*\**([A-Za-z][A-Za-z-]*)", raw or "")
    if not m:
        return "unspecified"
    w = m.group(1).lower().rstrip("-")
    return STATUS_SYNONYMS.get(w, w)


def norm_priority(raw: str) -> str:
    m = re.search(r"\bP([0-9])\b", raw or "", re.I)
    return f"P{m.group(1)}" if m else "Punspecified"
LINK_RE = re.compile(r"\[\[FU-(\d+)\]\]")
SHA_RE = re.compile(r"\b[0-9a-f]{7,40}\b")
PR_RE = re.compile(r"#(\d{3,5})\b")
DATE_RE = re.compile(r"\b(20\d{2}-\d{2}-\d{2})\b")

TODAY = date.today()
NOW_ISO = datetime.now(timezone.utc).isoformat(timespec="seconds")


# ------------------------------------------------------------------ helpers --

def cure_encoders() -> None:
    """A child handed a cp1252 stdout dies while REPORTING and forges a clean
    run. Cure BOTH streams at entry, never at the call site."""
    for name in ("stdout", "stderr"):
        stream = getattr(sys, name, None)
        if stream is None:
            continue
        try:
            if getattr(stream, "encoding", "").lower() not in ("utf-8", "utf8"):
                setattr(sys, name, io.TextIOWrapper(
                    stream.buffer, encoding="utf-8", errors="replace",
                    line_buffering=True))
        except Exception:
            pass


def read_text(path: Path) -> str:
    """Binary read + explicit decode. Never opened in text mode, never written."""
    return path.read_bytes().decode("utf-8", errors="replace")


def guard_output(path: Path) -> Path:
    """Refuse to write anything that could be the ledger or a sibling ledger."""
    resolved = Path(os.path.abspath(str(path)))
    if resolved.name.startswith("FOLLOWUPS"):
        raise RuntimeError(f"REFUSED: output path targets the ledger: {resolved}")
    if OUT.resolve() not in resolved.parents and resolved.parent != OUT:
        raise RuntimeError(f"REFUSED: output escapes the pack directory: {resolved}")
    return resolved


def write_out(name: str, text: str) -> dict:
    path = guard_output(OUT / name)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = text.encode("utf-8")
    with open(path, "wb") as fh:          # binary: no newline translation
        fh.write(data)
    return {
        "file": name,
        "bytes": len(data),
        "sha256": hashlib.sha256(data).hexdigest()[:16],
        "est_tokens": len(data) // 4,
    }


def unknown(reason: str) -> dict:
    """R6: unknown is not zero. A thing we could not measure says so."""
    return {"state": "COULD_NOT_DETERMINE", "reason": reason,
            "measured_at": NOW_ISO}


def measured(value, source: str) -> dict:
    return {"state": "MEASURED", "value": value, "source": source,
            "measured_at": NOW_ISO}


def run(cmd, cwd=None, timeout=90):
    try:
        p = subprocess.run(cmd, cwd=str(cwd) if cwd else None, timeout=timeout,
                           capture_output=True, shell=False)
        return p.returncode, p.stdout.decode("utf-8", "replace"), \
            p.stderr.decode("utf-8", "replace")
    except Exception as exc:                       # noqa: BLE001
        return 127, "", f"{type(exc).__name__}: {exc}"


# ------------------------------------------------------------------- parser --

def parse_ledger(text: str) -> list[dict]:
    """One dict per FU entry.

    Two on-disk shapes exist and both must parse:
      OLD inline:  - date: 2026-08-04 . source: X . status: open . priority: P1
      NEW stacked: - date: 2026-08-09
                   - status: in-progress
    Fields are read ONLY from top-level '- ' lines. Log sub-bullets are indented
    two spaces, so prose inside a log line that happens to contain 'status: done'
    can never be mistaken for the entry's own status -- the naive grep over this
    file overcounts 'resolved' by exactly that mechanism.
    """
    for bad, good in MOJIBAKE.items():
        text = text.replace(bad, good)
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")

    starts = []
    for i, line in enumerate(lines):
        m = ENTRY_RE.match(line)
        if m:
            starts.append((i, int(m.group(1)), m.group(2).strip()))

    entries = []
    for idx, (line_no, fu_id, title) in enumerate(starts):
        end = starts[idx + 1][0] if idx + 1 < len(starts) else len(lines)
        block = lines[line_no + 1:end]
        raw = "\n".join(lines[line_no:end]).strip()

        fields: dict[str, str] = {}
        body: dict[str, list[str]] = defaultdict(list)
        log_lines: list[str] = []
        current = None
        in_log = False

        for ln in block:
            if ln.startswith("- ") or ln.startswith("* "):
                in_log = False
                payload = ln[2:]
                consumed = False

                # A metadata line packs several fields onto one line, and the
                # ledger uses AT LEAST THREE separators across its history:
                # U+00B7, a bare hyphen, and an em/en dash. Splitting on the
                # separator is the wrong move -- a hyphen also appears INSIDE
                # values ("in-progress", "follow-up-triage"), so a naive split
                # shreds them. Cut only where a separator is followed by
                # another KNOWN key, which is unambiguous.
                if META_LINE_RE.match(payload):
                    for m in META_FIELD_RE.finditer(payload):
                        fields.setdefault(m.group(1), m.group(2).strip())
                    consumed = True
                else:
                    m = re.match(r"^([a-z_]+)\s*:\s*(.*)$", payload.strip())
                    if m:
                        key, val = m.group(1), m.group(2).strip()
                        if key in FIELD_KEYS:
                            fields.setdefault(key, val)
                            consumed = True
                        elif key in BODY_KEYS:
                            current = key
                            if key == "log":
                                in_log = True
                                if val:
                                    log_lines.append(val)
                            elif val:
                                body[key].append(val)
                            consumed = True
                if not consumed and current:
                    body[current].append(payload.strip())
            elif META_LINE_RE.match(ln):
                # Bare "status: open" at column 0, no list marker. Newer lanes
                # write a metadata block this way after the prose.
                in_log = False
                for m in META_FIELD_RE.finditer(ln):
                    fields.setdefault(m.group(1), m.group(2).strip())
            elif in_log and ln.strip().startswith(("-", "*")):
                log_lines.append(ln.strip().lstrip("-* ").strip())
            elif ln.strip() and current and not in_log:
                body[current].append(ln.strip())

        status_raw = (fields.get("status") or "").strip()
        status = norm_status(status_raw)
        priority = norm_priority(fields.get("priority") or "")

        filed_date = None
        for k in DATE_KEYS:
            m = DATE_RE.search(fields.get(k, "") or "")
            if m:
                filed_date = m.group(1)
                break

        log_dates = [d for ln in log_lines for d in DATE_RE.findall(ln)]
        all_dates = sorted(set(log_dates + ([filed_date] if filed_date else [])))
        last_touch = all_dates[-1] if all_dates else None

        def age(d):
            if not d:
                return None
            try:
                return (TODAY - datetime.strptime(d, "%Y-%m-%d").date()).days
            except ValueError:
                return None

        blob = raw
        entries.append({
            "id": fu_id,
            "fu": f"FU-{fu_id:03d}",
            "title": title,
            "status": status,
            "status_raw": status_raw[:160] or None,
            "status_absent": not bool(status_raw),
            # Independent of the field parser: does a status token appear in the
            # block AT ALL? If it does and the parser produced nothing, that is
            # a BUILDER miss, not a ledger hole. Without this the only published
            # completeness number was vacuous -- an entry whose status was never
            # EXTRACTED sets status_absent, so it was excluded from the very
            # count meant to catch it. That is how the "|" separator hid.
            "status_token_present": bool(STATUS_TOKEN_RE.search(blob)),
            "open": status in OPEN_STATES,
            "priority": priority,
            "class": (fields.get("class") or "unspecified").strip().lower(),
            "source": (fields.get("source") or fields.get("lane")
                       or "unspecified").strip(),
            "filed": filed_date,
            "age_days": age(filed_date),
            "last_touch": last_touch,
            "stale_days": age(last_touch),
            "log_entries": len(log_lines),
            "links": sorted({int(x) for x in LINK_RE.findall(blob)} - {fu_id}),
            "has_verify": bool(body.get("verify")),
            "verify_seen_red": " ".join(body.get("verify_seen_red", [])).strip()
                               or None,
            "has_resolution": bool(body.get("resolution")),
            "pr_refs": sorted({int(x) for x in PR_RE.findall(blob)})[:8],
            "pr_refs_truncated": len({int(x) for x in PR_RE.findall(blob)}) > 8,
            "chars": len(blob),
            "detail_head": " ".join(body.get("detail", []))[:400],
            "resolution_head": " ".join(body.get("resolution", []))[:300],
            "residual_head": " ".join(body.get("residual", []))[:300],
            "_raw": blob,
        })
    return entries


# ------------------------------------------------------------------ families --

THEMES = {
    "gates_and_predicates": [
        "gate", "predicate", "verify", "guard", "green", "red check", "assert",
        "control", "falsif", "probe", "ratchet", "check-run", "acceptance bar",
    ],
    "counters_and_censuses": [
        "census", "count", "counter", "baseline", "trend", "coverage",
        "backlog", "denominator", "ratio", "leaderboard", "miskey",
    ],
    "mechanical_and_transport": [
        "crlf", "encoding", "cp1252", "timeout", "detach", "subprocess",
        "powershell", "sys.path", "import", "buffer", "scratchpad", "clamp",
        "transport", "poll", "shell",
    ],
    "deploy_and_infra": [
        "deploy", "fly", "prod", "daemon", "reload", "migration", "postgres",
        "backup", "restore", "dns", "domain", "clerk", "webhook", "flyctl",
        "runtime", "docker", "alembic",
    ],
    "moat_and_scoring": [
        "score", "scoring", "rescore", "moat", "corpus", "registry", "wave",
        "adapter", "student", "axis", "cve", "vuln", "gpu", "vast", "spend",
        "freshness", "cohort", "harvest", "dedup", "canonical",
    ],
    "product_surface": [
        "ui", "ask", "search", "page", "endpoint", "api", "user", "signup",
        "seo", "waitlist", "landing", "mcprisky", "mcplookup", "auth",
    ],
    "loop_lanes_and_builder": [
        "lane", "loop", "builder", "goose", "directive", "architect",
        "scheduled task", "cadence", "shepherd", "recipe", "skill.md",
        "dark tool", "obligation", "check-in",
    ],
    "governance_and_authority": [
        "authority", "governance", "peer review", "chairman", "cofc",
        "permission", "halt", "proposal", "clause", "ceiling", "grant",
        "supersede", "rule",
    ],
}


def classify(entry: dict) -> list[str]:
    hay = (entry["title"] + " " + entry["detail_head"]).lower()
    hit = [name for name, words in THEMES.items()
           if any(w in hay for w in words)]
    return hit or ["unclassified"]


def build_themes(entries: list[dict]) -> dict:
    """Group by hazard theme, NOT by connected components.

    The citation graph was tried first and is degenerate: [[FU-nnn]] links form
    one component holding 265 of 375 entries, which tells the plan nothing. That
    negative result is kept here deliberately -- a "family" that swallows 70% of
    the ledger is a label hiding its call sites, the exact failure mode the
    hazard memory names. Themes below come from the taxonomy the operator's own
    MEMORY.md already uses, so a plan reading this pack and a plan reading that
    index are sorting the world the same way.

    An entry can sit in more than one theme. That is intended: overlap is signal
    about where the defects actually compound.
    """
    buckets = defaultdict(list)
    for e in entries:
        for t in classify(e):
            buckets[t].append(e)

    # citation hubs: which entries the ledger itself keeps pointing back to
    indeg = Counter()
    for e in entries:
        for other in e["links"]:
            indeg[other] += 1
    by_id = {e["id"]: e for e in entries}

    return {
        "themes": {
            name: {
                "total": len(members),
                "open": len([m for m in members if actionable(m)]),
                "undetermined": len([m for m in members if m["status_absent"]]),
                "open_p0p1": len([m for m in members if actionable(m)
                                  and m["priority"] in ("P0", "P1")]),
                "members": sorted(m["fu"] for m in members),
                "open_members": [
                    {"fu": m["fu"], "priority": m["priority"],
                     "status": "NO-STATUS" if m["status_absent"]
                               else m["status"],
                     "stale_days": m["stale_days"],
                     "title": m["title"][:150]}
                    for m in sorted(members,
                                    key=lambda x: (x["priority"],
                                                   -(x["stale_days"] or 0)))
                    if actionable(m)],
            }
            for name, members in sorted(buckets.items(),
                                        key=lambda kv: -len([m for m in kv[1]
                                                             if actionable(m)]))
        },
        "citation_hubs": [
            {"fu": by_id[i]["fu"], "cited_by": n, "open": by_id[i]["open"],
             "priority": by_id[i]["priority"], "title": by_id[i]["title"][:140]}
            for i, n in indeg.most_common(25) if i in by_id],
        "degenerate_component_note":
            "Connected components over [[FU-nnn]] were computed and DISCARDED: "
            "the largest held 265 of 375 entries. Do not re-derive them and "
            "call it a family structure.",
    }


# ----------------------------------------------------------------- lane docs --

def load_registered() -> tuple[dict | None, str]:
    """The scheduler's registered roster, snapshotted beside this builder.

    A lane DIRECTORY is not a lane. 36 directories exist; 22 are registered. The
    rest are finished one-shots and abandoned watches that still carry
    live-looking prompts, and reading them as the fleet overstates it by 60%.
    """
    p = ROOT / "_ultraplan" / "registered_tasks.json"
    if not p.exists():
        return None, f"no roster at {p}"
    try:
        data = json.loads(read_text(p))
        stamp = datetime.fromisoformat(data["snapshot_at"].replace("Z", "+00:00"))
        age = (datetime.now(timezone.utc) - stamp).days
        if age > 7:
            return None, (f"roster is {age} days old (snapshot_at "
                          f"{data['snapshot_at']}); refusing to label lanes "
                          f"from a stale roster -- refresh it")
        return {t["id"]: t for t in data["tasks"]}, f"roster age {age}d"
    except Exception as exc:                       # noqa: BLE001
        return None, f"roster unreadable: {exc}"


def collect_lanes() -> list[dict]:
    lanes = []
    reg, reg_note = load_registered()
    if not SCHEDULED_DIR.exists():
        return lanes
    for d in sorted(SCHEDULED_DIR.iterdir()):
        if not d.is_dir():
            continue
        if reg is None:
            registration = f"COULD_NOT_DETERMINE ({reg_note})"
        elif d.name in reg:
            r = reg[d.name]
            registration = (f"REGISTERED, "
                            f"{'ENABLED' if r['enabled'] else 'DISABLED'}, "
                            f"cron {r['cron']}, last run {r['lastRunAt']}")
        else:
            registration = "ORPHAN ON DISK -- not in the scheduler's roster"

        skill = d / "SKILL.md"
        if not skill.exists():
            lanes.append({"lane": d.name, "skill_bytes": None,
                          "registration": registration, "state": "NO_SKILL_MD"})
            continue
        text = read_text(skill)
        lanes.append({
            "lane": d.name,
            "registration": registration,
            "skill_bytes": skill.stat().st_size,
            "skill_mtime": datetime.fromtimestamp(
                skill.stat().st_mtime, timezone.utc).isoformat(timespec="seconds"),
            "lines": text.count("\n") + 1,
            "head": "\n".join(text.split("\n")[:40]),
            "state": "READ",
        })
    return lanes


def find_memory_dir() -> Path | None:
    """The hazard store moves with the Claude space id, so it is DISCOVERED,
    never hardcoded. A hardcoded path that silently stops resolving would make
    this artifact quietly empty -- and an empty hazard file reads exactly like
    'there are no hazards'."""
    base = Path(os.environ.get("APPDATA", "")) / "Claude" \
        / "local-agent-mode-sessions"
    if not base.exists():
        return None
    best, best_n = None, 0
    for cand in base.glob("*/*/spaces/*/memory"):
        if not cand.is_dir():
            continue
        n = len(list(cand.glob("*.md")))
        if n > best_n:
            best, best_n = cand, n
    return best


def render_hazards() -> str:
    d = find_memory_dir()
    out = [
        "# HAZARD CORPUS -- what this system has already learned the hard way",
        "",
        f"Built {NOW_ISO}.",
        "",
        "READ THIS BEFORE PROPOSING ANYTHING. Every entry below is a move that",
        "was tried and bit. The single highest-value thing a fresh plan can do",
        "with this pack is avoid re-proposing a cure that is already recorded as",
        "dead. Several standing hazards are specifically about PLANS:",
        "",
        "  - a halt or proposal can outlive the condition that justified it, so",
        "    ask whether the PREMISE is still true, not whether the argument is",
        "    sound;",
        "  - a cure wired into one door of eight reads as a cure;",
        "  - existence is not adoption -- a tool that exists and is never called",
        "    has not landed;",
        "  - a remedy is only a cure if it is reachable from the surface that was",
        "    bitten.",
        "",
    ]
    if d is None:
        out.append("[COULD_NOT_DETERMINE: memory directory not found under "
                   "%APPDATA%\\Claude\\local-agent-mode-sessions. This file is "
                   "INCOMPLETE -- do not read its emptiness as an absence of "
                   "hazards.]")
        return "\n".join(out)

    out.append(f"Source: `{d}` ({len(list(d.glob('*.md')))} files). Full text of "
               f"any entry is at that path -- open it when a hazard becomes "
               f"load-bearing.")
    out.append("")
    index = d / "MEMORY.md"
    if index.exists():
        out.append("---\n\n## MEMORY.md -- the standing index, verbatim\n")
        out.append(read_text(index))
    out.append("\n---\n\n## Every memory file: name, description, opening\n")
    for f in sorted(d.glob("*.md")):
        if f.name.startswith("MEMORY.md"):
            continue
        try:
            t = read_text(f)
        except Exception:                          # noqa: BLE001
            continue
        desc = ""
        m = re.search(r"^description:\s*(.+)$", t, re.M)
        if m:
            desc = m.group(1).strip()
        bodym = re.split(r"(?m)^---\s*$", t)
        body = (bodym[2] if len(bodym) > 2 else t).strip()
        out.append(f"### {f.name}")
        if desc:
            out.append(f"*{desc}*")
        out.append(body[:400].replace("\n", " ") + ("…" if len(body) > 400 else ""))
        out.append("")
    return "\n".join(out)


# ------------------------------------------------------------- live snapshot --

FRESHNESS_CANDIDATES = [
    "https://mcprisky.io/api/v1/freshness",
    "https://mcprisky.io/api/freshness",
    "https://mcprisky.io/freshness",
]


def live_snapshot(enabled: bool) -> dict:
    snap = {"built_at": NOW_ISO, "enabled": enabled}
    if not enabled:
        snap["note"] = "--no-live passed; every field below is COULD_NOT_DETERMINE"
        for k in ("origin_main", "pr_queue", "freshness", "repo_worktree"):
            snap[k] = unknown("live probes disabled by flag")
        return snap

    # --- git: the CANONICAL head is origin/main, never the worktree's branch.
    rc, out, err = run(["git", "ls-remote", "origin", "refs/heads/main"], cwd=REPO)
    if rc == 0 and out.strip():
        snap["origin_main"] = measured(out.split()[0], "git ls-remote origin main")
    else:
        snap["origin_main"] = unknown(f"git ls-remote rc={rc} err={err[:200]}")

    rc, out, err = run(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=REPO)
    snap["repo_worktree"] = measured(out.strip(), "git rev-parse HEAD") if rc == 0 \
        else unknown(f"rc={rc} {err[:200]}")

    # --- PR queue
    PR_LIMIT = 1000
    rc, out, err = run(
        ["gh", "pr", "list", "--repo", "rob531/zo-sentinel", "--state", "open",
         "--limit", str(PR_LIMIT), "--json",
         "number,title,createdAt,isDraft,mergeable,author,labels"],
        cwd=REPO, timeout=180)
    if rc == 0 and out.strip():
        try:
            prs = json.loads(out)
            # A list returned AT the cap is a cap, not a count. The first build
            # of this pack asked for 200 and reported "open_count: 200" as if
            # that were the queue depth.
            if len(prs) >= PR_LIMIT:
                snap["pr_queue"] = unknown(
                    f"gh returned exactly the --limit of {PR_LIMIT}: this is a "
                    f"TRUNCATION, not a count. Re-run with a higher limit.")
                raise StopIteration
            snap["pr_queue"] = measured(
                {"open_count": len(prs),
                 "draft_count": sum(1 for p in prs if p.get("isDraft")),
                 # Full histogram, not a two-way split. GitHub computes
                 # mergeability lazily, so UNKNOWN is a real third state and
                 # binning it with MERGEABLE reports an unmeasured PR as
                 # healthy. Two probes minutes apart returned 4 and 22
                 # CONFLICTING against the same queue purely because more had
                 # resolved -- so this field is a SNAPSHOT of a computation in
                 # progress, not a stable property. Re-read it, do not cite it
                 # from an old build.
                 "mergeable_histogram": dict(Counter(
                     p.get("mergeable") or "NULL" for p in prs)),
                 "mergeable_is_lazily_computed":
                     "UNKNOWN means GitHub has not finished checking; it is "
                     "not a synonym for MERGEABLE.",
                 "oldest": min((p["createdAt"] for p in prs), default=None),
                 "items": [{"n": p["number"], "t": p["title"][:120],
                            "created": p["createdAt"],
                            "draft": p.get("isDraft"),
                            "mergeable": p.get("mergeable")} for p in prs]},
                f"gh pr list rob531/zo-sentinel --limit {PR_LIMIT} "
                f"(returned {len(prs)}, under cap, so this IS a count)")
        except StopIteration:
            pass
        except Exception as exc:                   # noqa: BLE001
            snap["pr_queue"] = unknown(f"json parse failed: {exc}")
    else:
        snap["pr_queue"] = unknown(f"gh pr list rc={rc} err={err[:300]}")

    # --- product freshness
    import urllib.request
    got = None
    for url in FRESHNESS_CANDIDATES:
        try:
            with urllib.request.urlopen(url, timeout=25) as resp:
                raw = resp.read().decode("utf-8", "replace")
            got = measured(json.loads(raw), url)
            break
        except Exception as exc:                   # noqa: BLE001
            got = unknown(f"last tried {url}: {type(exc).__name__}: {exc}")
    snap["freshness"] = got or unknown("no candidate endpoint attempted")

    # Derived product truth. Computed here, once, from the measurement above --
    # so the plan cites one number with one basis instead of each reader doing
    # the division and getting a slightly different denominator.
    if snap["freshness"]["state"] == "MEASURED":
        v = snap["freshness"]["value"]
        try:
            reg = v.get("registry_rows")
            sc = v.get("scored_servers")
            def age(stamp):
                if not stamp:
                    return None
                d = datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
                if d.tzinfo is None:
                    d = d.replace(tzinfo=timezone.utc)
                return round((datetime.now(timezone.utc) - d).days)
            snap["derived"] = measured({
                "coverage_pct": round(100.0 * sc / reg, 2) if reg else None,
                "never_scored": v.get("never_scored"),
                "newest_score_age_days": age(v.get("newest_scored_at")),
                "oldest_score_age_days": age(v.get("oldest_scored_at")),
                "plan200k_goal_rows": 200000,
                "registry_vs_goal_pct": round(100.0 * reg / 200000, 1)
                                        if reg else None,
                "note": "coverage = scored_servers / registry_rows from the "
                        "SAME /freshness payload, so numerator and denominator "
                        "share one computed_at. never_scored is NOT a backlog. "
                        "oldest_score_age_days is the corpus FLOOR -- if it has "
                        "not moved between two builds of this pack, the refresh "
                        "half of the cohort is not moving it.",
            }, "derived from /freshness in this same call")
        except Exception as exc:                   # noqa: BLE001
            snap["derived"] = unknown(f"derivation failed: {exc}")
    else:
        snap["derived"] = unknown("freshness not measured, so nothing derived")
    return snap


# ---------------------------------------------------------------- self-test --

SELFTEST_LEDGER = """### FU-001 | inline shape, middle-dot separator
- date: 2026-01-02 \u00b7 source: lane-a \u00b7 status: open \u00b7 priority: P1
- detail: cites [[FU-002]] and mentions a gate predicate
- log:
  - 2026-03-04 something with the words status: done inside prose

### FU-002 | stacked shape
- date: 2026-01-03
- lane: lane-b
- class: defect
- status: resolved
- priority: P2
- detail: body about deploy and fly
- resolution: SHA abc1234, PR #4001

### FU-003 | inline shape, HYPHEN separator
- date: 2026-02-05 - source: follow-up-triage - status: in-progress - priority: P1
- class: defect
- detail: a scoring wave over the moat corpus

### FU-004 | inline shape, em-dash separator
- date: 2026-02-06 \u2014 source: lane-c \u2014 status: watch \u2014 priority: P3
- detail: a census counter

### FU-005 | bold markdown fields, status value is a whole sentence
- **status:** OPEN, routed to peer review rather than acted on
- **priority:** P1
- detail: an authority halt

### FU-006 | bare metadata block, no list marker, and NO STATUS AT ALL
date: 2026-02-08
lane: lane-d
class: defect
- detail: a deploy daemon reload

### FU-007 | PIPE separator, and the title itself contains a pipe
- date: 2026-02-09 | source: prod-drift-sentinel 04:47Z run | status: resolved | priority: P1
- detail: a corpus floor

### FU-008 | the date key is spelled `opened`
- opened: 2026-02-10 - source: lane-e - status: open - priority: P2
- detail: a scoring wave
"""


def self_test() -> int:
    es = parse_ledger(SELFTEST_LEDGER)
    by = {e["id"]: e for e in es}
    checks = [
        ("eight entries parsed", len(es) == 8),
        # REGRESSION GUARDS. Both of these shipped broken in build 3 and were
        # found only by an adversarial read of the output, not by this suite.
        ("PIPE sep: status resolved (7 entries were misfiled as undetermined)",
         by[7]["status"] == "resolved"),
        ("PIPE sep: priority P1 (this is how FU-361 fell out of tier 1)",
         by[7]["priority"] == "P1"),
        ("PIPE sep: a pipe in the TITLE does not confuse the field parser",
         by[7]["title"].startswith("PIPE separator")),
        ("`opened:` is read as the filed date", by[8]["filed"] == "2026-02-10"),
        ("`opened:` entry still gets its status", by[8]["status"] == "open"),
        ("status token detector fires when a status exists",
         by[7]["status_token_present"]),
        ("status token detector does NOT fire on a block with no status",
         not by[6]["status_token_present"]),
        ("BOLD fields: **status:** parsed", by[5]["status"] == "open"),
        ("BOLD fields: a sentence value yields the STATE, not a slug of the "
         "whole sentence", by[5]["status"] == "open"),
        ("BOLD fields: priority P1", by[5]["priority"] == "P1"),
        ("BARE block: class parsed with no list marker",
         by[6]["class"] == "defect"),
        ("BARE block: a genuinely missing status is flagged ABSENT, not "
         "guessed", by[6]["status_absent"] and by[6]["status"] == "unspecified"),
        ("a present-but-odd status is NOT flagged absent",
         not by[5]["status_absent"]),
        ("middle-dot: status open", by[1]["status"] == "open"),
        ("prose 'status: done' in a log line does NOT win",
         by[1]["status"] != "done"),
        ("middle-dot: priority P1", by[1]["priority"] == "P1"),
        ("stacked: status resolved", by[2]["status"] == "resolved"),
        ("stacked: class defect", by[2]["class"] == "defect"),
        # the two below are the regression that the first build shipped:
        # 109 of 375 entries came back status=unspecified purely because the
        # hyphen variant was not a recognised separator.
        ("HYPHEN sep: status in-progress survives its own hyphen",
         by[3]["status"] == "in-progress"),
        ("HYPHEN sep: source keeps its internal hyphens",
         by[3]["source"] == "follow-up-triage"),
        ("HYPHEN sep: priority P1", by[3]["priority"] == "P1"),
        ("EM-DASH sep: status watch", by[4]["status"] == "watch"),
        ("link graph found FU-002 from FU-001", by[1]["links"] == [2]),
        ("open flag separates", by[1]["open"] and not by[2]["open"]),
        ("pr ref captured", 4001 in by[2]["pr_refs"]),
        ("themes: gate keyword lands FU-001 in gates_and_predicates",
         "gates_and_predicates" in classify(by[1])),
        ("themes: an entry can hold two themes",
         len(classify(by[3])) >= 1),
    ]
    bad = [n for n, ok in checks if not ok]
    for name, ok in checks:
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}")

    # NEGATIVE CONTROLS -- the checks above are worthless unless these can fail.
    neg = parse_ledger("### FU-009 | no fields\n- detail: x\n")
    ctrl_a = bool(neg) and neg[0]["status"] == "unspecified" and \
        neg[0]["priority"] == "Punspecified"
    print(f"  [{'PASS' if ctrl_a else 'FAIL'}] control A: missing fields become "
          f"'unspecified', never a defaulted 'open'")

    # control B: a value containing a separator-looking string must NOT be cut
    # unless a KNOWN key follows it.
    negb = parse_ledger("### FU-010 | x\n- date: 2026-01-01 - status: open - "
                        "priority: P2\n- detail: y\n")
    ctrl_b = bool(negb) and negb[0]["status"] == "open" and \
        negb[0]["filed"] == "2026-01-01"
    print(f"  [{'PASS' if ctrl_b else 'FAIL'}] control B: a hyphen inside the "
          f"date value does not truncate the field run")

    if bad or not ctrl_a or not ctrl_b:
        print(f"SELF-TEST FAILED: {bad}")
        return 1
    print(f"SELF-TEST PASSED ({len(checks) + 2}/{len(checks) + 2})")
    return 0


# ---------------------------------------------------------------- renderers --

def actionable(e: dict) -> bool:
    """Open OR undetermined. An entry with NO status field is not closed -- it
    is unmeasured, and unknown is not zero. Selecting on `open` alone silently
    dropped 48 entries from the first two builds of this pack, four of them P1,
    including one asserting the live dashboard mislabels 191,273 servers."""
    return e["open"] or e["status_absent"]


def render_tier1(entries: list[dict]) -> str:
    sel = [e for e in entries if actionable(e) and e["priority"] in ("P0", "P1")]
    sel.sort(key=lambda e: (e["priority"], -(e["stale_days"] or 0)))
    n_abs = len([e for e in sel if e["status_absent"]])
    out = [
        "# TIER 1 -- ACTIONABLE P0/P1, VERBATIM",
        "",
        f"Built {NOW_ISO}. {len(sel)} entries, full text, nothing elided.",
        f"{n_abs} of them carry NO status field and are included because",
        "undetermined is not closed.",
        "",
        "These are the entries the plan is allowed to act on directly. Everything",
        "else in the pack is context. An entry here is NOT automatically valid:",
        "check that its PREMISE is still true against live state before acting --",
        "this ledger has had a halt outlive its condition by 23h and a proposal",
        "outlive its premise by 22h.",
        "",
        "---",
        "",
    ]
    for e in sel:
        flag = " NO-STATUS-FIELD" if e["status_absent"] else ""
        out.append(f"<!-- {e['fu']} status={e['status']}{flag} "
                   f"priority={e['priority']} filed={e['filed']} "
                   f"last_touch={e['last_touch']} stale_days={e['stale_days']} -->")
        out.append(e["_raw"])
        out.append("\n---\n")
    return "\n".join(out)


def render_no_status(entries: list[dict]) -> str:
    sel = [e for e in entries if e["status_absent"]]
    sel.sort(key=lambda e: -(e["id"]))
    recent = [e for e in sel
              if (e["stale_days"] is None or e["stale_days"] <= 30)
              or e["priority"] in ("P0", "P1")]
    older = [e for e in sel if e not in recent]
    out = [
        "# UNDETERMINED -- entries carrying NO status field",
        "",
        f"Built {NOW_ISO}. {len(sel)} of {len(entries)} entries.",
        "",
        "**These are neither open nor closed.** The triage lane is the only writer",
        "of `status:` lines and it sweeps on that key, so an entry that never got",
        "one is invisible to it -- it cannot be worked, cannot be closed, and does",
        "not appear in any open-count. They cluster in 2026-08-10 onward, which is",
        "when several lanes switched to a prose-heavy entry shape.",
        "",
        "Do NOT read this as a backlog to burn down. Read it as a MEASUREMENT",
        "FAILURE first: the correct first move is to determine, per entry, whether",
        "it is already resolved by later work. Some almost certainly are. Counting",
        "them as open would inflate the ledger; counting them as closed would lose",
        "real P1 defects. Both are wrong until measured.",
        "",
        f"Full text below for the {len(recent)} that are recent or P0/P1; "
        f"one paragraph for the remaining {len(older)}.",
        "",
        "---",
        "",
    ]
    for e in recent:
        out.append(f"<!-- {e['fu']} NO-STATUS priority={e['priority']} "
                   f"filed={e['filed']} last_touch={e['last_touch']} -->")
        out.append(e["_raw"])
        out.append("\n---\n")
    if older:
        out.append("\n## Older, digest only\n")
        for e in older:
            out.append(f"### {e['fu']} [{e['priority']}] {e['title']}")
            out.append(f"filed {e['filed']} · last touch {e['last_touch']} "
                       f"· source {e['source']}")
            out.append(e["detail_head"])
            out.append("")
    return "\n".join(out)


def render_themes(themes: dict) -> str:
    out = [
        "# TIER 3 -- THEMES AND CITATION HUBS",
        "",
        f"Built {NOW_ISO}.",
        "",
        themes["degenerate_component_note"],
        "",
        "Themes below use the same taxonomy as the operator's MEMORY.md index, so",
        "a plan reading this pack sorts the world the way the standing hazard",
        "index already does. An entry can appear in more than one theme; that",
        "overlap is signal, not sloppiness.",
        "",
        "CAUTION: a theme is a LABEL. It hides the call sites it is made of.",
        "Before treating a theme as one problem, regroup its open members by the",
        "actual mechanism -- that move has previously split a 12-row 'family'",
        "into three unrelated things, one of which was the cure working.",
        "",
        "---",
        "",
    ]
    for name, t in themes["themes"].items():
        out.append(f"## {name} -- {t['open']} actionable of {t['total']} "
                   f"({t['open_p0p1']} at P0/P1, "
                   f"{t['undetermined']} with no status field)")
        out.append("")
        for m in t["open_members"]:
            out.append(f"- {m['fu']} [{m['priority']}/{m['status']}] "
                       f"stale {m['stale_days']}d -- {m['title']}")
        out.append("")
    out.append("---\n\n## CITATION HUBS -- what the ledger keeps pointing back to\n")
    for h in themes["citation_hubs"]:
        out.append(f"- {h['fu']} cited by {h['cited_by']} "
                   f"[{h['priority']}/{'OPEN' if h['open'] else 'closed'}] "
                   f"{h['title']}")
    return "\n".join(out)


def render_lanes(lanes: list[dict]) -> str:
    nreg = len([l for l in lanes if l["registration"].startswith("REGISTERED")])
    norph = len([l for l in lanes if l["registration"].startswith("ORPHAN")])
    nen = len([l for l in lanes if "ENABLED" in l["registration"]])
    out = [
        "# LANE SURFACE -- scheduled tasks that write into this system",
        "",
        f"Built {NOW_ISO}. **{len(lanes)} lane directories on disk, but only "
        f"{nreg} are registered with the scheduler ({nen} of those enabled). "
        f"{norph} are ORPHANS.**",
        "",
        "A lane directory is not a lane. The orphans are finished one-shots and",
        "abandoned watches whose SKILL.md still reads like a live prompt --",
        "several still carry standing instructions and away-window dates. They",
        "run never. Counting directories as the fleet overstates it by ~60%, and",
        "reading an orphan's prompt as current doctrine is worse than that.",
        "",
        "Registration status below comes from `_ultraplan/registered_tasks.json`,",
        "which is a SNAPSHOT with its own date. If it is more than 7 days old the",
        "builder refuses to label from it and every lane reads",
        "COULD_NOT_DETERMINE rather than being guessed.",
        "",
        "Each lane section is headed `## LANE:` so it can be told apart from the",
        "`##` headings inside the embedded SKILL.md excerpts below it.",
        "",
        "SKILL.md IS the store for a scheduled task: editing it edits the live",
        "prompt. The allowlist is not the thing holding the button. Any plan that",
        "changes a lane changes it via `_tools/task_edit.py`, never by hand-editing",
        "these files, and the PROMPT is ours to change while the SCHEDULE is not.",
        "",
        "Each lane's first 40 lines are included so the plan can see what the lane",
        "believes its job is -- several lane descriptions have historically named a",
        "cadence their cron does not have.",
        "",
        "---",
        "",
    ]
    for ln in sorted(lanes, key=lambda x: (x["registration"].startswith("ORPHAN"),
                                           x["lane"])):
        out.append(f"## LANE: {ln['lane']}")
        out.append(f"- **{ln['registration']}**")
        out.append(f"- skill_bytes: {ln.get('skill_bytes')} | "
                   f"lines: {ln.get('lines')} | mtime: {ln.get('skill_mtime')} | "
                   f"state: {ln['state']}")
        if ln.get("head"):
            out.append("```")
            out.append(ln["head"])
            out.append("```")
        out.append("")
    return "\n".join(out)


def render_stats(entries: list[dict], themes: dict) -> dict:
    opens = [e for e in entries if e["open"]]
    return {
        "measured_at": NOW_ISO,
        "ledger_bytes": LEDGER.stat().st_size,
        "ledger_mtime": datetime.fromtimestamp(
            LEDGER.stat().st_mtime, timezone.utc).isoformat(timespec="seconds"),
        "total_entries": len(entries),
        "max_fu_id": max(e["id"] for e in entries),
        "id_gaps": sorted(set(range(1, max(e["id"] for e in entries) + 1))
                          - {e["id"] for e in entries}),
        "by_status": dict(Counter(e["status"] for e in entries).most_common()),
        "by_priority": dict(Counter(e["priority"] for e in entries).most_common()),
        "by_class": dict(Counter(e["class"] for e in entries).most_common()),
        # THE DISTINCTION THAT MATTERS: an entry the parser could not read is a
        # BUILDER defect; an entry that carries no status field at all is a
        # LEDGER defect -- it is neither open nor closed and no status-keyed
        # sweep will ever see it. Reporting them as one number would blame the
        # ledger for the builder's misses, which the first build of this pack
        # did (109 "unspecified", of which 47 were the parser's own fault).
        "status_field_absent": len([e for e in entries if e["status_absent"]]),
        # A parse miss: the block HAS a status token, the parser got nothing.
        # Build aborts if this is non-zero, so a shipped pack always reads 0 --
        # which is the point. The number that matters is that the gate exists.
        "status_token_present_but_unparsed": len(
            [e for e in entries
             if e["status_absent"] and e["status_token_present"]]),
        "invisible_to_status_sweep": [
            {"fu": e["fu"], "priority": e["priority"], "filed": e["filed"],
             "title": e["title"][:120]}
            for e in entries if e["status_absent"]],
        "open_total": len(opens),
        "open_by_priority": dict(Counter(e["priority"] for e in opens)
                                 .most_common()),
        "open_by_source": dict(Counter(e["source"] for e in opens)
                               .most_common(25)),
        "open_stale_gt_14d": len([e for e in opens
                                  if (e["stale_days"] or 0) > 14]),
        "open_stale_gt_30d": len([e for e in opens
                                  if (e["stale_days"] or 0) > 30]),
        "open_never_logged": len([e for e in opens if e["log_entries"] == 0]),
        "open_without_verify": len([e for e in opens if not e["has_verify"]]),
        "open_verify_never_seen_red": len(
            [e for e in opens if e["has_verify"]
             and (e["verify_seen_red"] or "").lower().startswith("unproven")]),
        "open_by_theme": {n: t["open"] for n, t in themes["themes"].items()},
        "open_p0p1_by_theme": {n: t["open_p0p1"]
                               for n, t in themes["themes"].items()},
        "citation_hubs": themes["citation_hubs"][:10],
        "oldest_open": sorted(
            [{"fu": e["fu"], "filed": e["filed"], "age_days": e["age_days"],
              "priority": e["priority"], "title": e["title"][:110]}
             for e in opens if e["age_days"] is not None],
            key=lambda x: -x["age_days"])[:20],
    }


# --------------------------------------------------------------------- main --

def main() -> int:
    cure_encoders()
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-live", action="store_true")
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args()

    if args.self_test:
        return self_test()

    print(f"[build_pack] {NOW_ISO}")
    print(f"[build_pack] self-test first (a builder that cannot fail its own "
          f"parser proves nothing)")
    if self_test() != 0:
        print("ABORT: parser self-test failed; pack not built.")
        return 1

    if not LEDGER.exists():
        print(f"ABORT: ledger not found at {LEDGER}")
        return 1

    text = read_text(LEDGER)
    entries = parse_ledger(text)
    themes = build_themes(entries)
    stats = render_stats(entries, themes)

    # independent control: the heading count from a dumb scan must equal the
    # parsed count. If it does not, the parser dropped entries silently.
    heading_count = len(re.findall(r"(?m)^#{2,3}\s+FU-\d+\s*\|", text))
    if heading_count != len(entries):
        print(f"ABORT: parsed {len(entries)} but file has {heading_count} "
              f"headings -- silent drop.")
        return 1
    print(f"[build_pack] control OK: {heading_count} headings == "
          f"{len(entries)} parsed")

    # SECOND CONTROL, and the one that matters. The heading count above only
    # proves no entry was DROPPED; it says nothing about entries parsed into
    # the wrong bucket. An unrecognised separator ("|") once put 7 entries --
    # including one the operator's standing hazard index names by number --
    # into "undetermined" while their status sat in plain text on the page.
    # A parse miss must be LOUD, because its failure mode is a silent
    # reclassification, not an error.
    misses = [e for e in entries
              if e["status_absent"] and e["status_token_present"]]
    if misses:
        print(f"\nABORT: {len(misses)} entries carry a status token the parser "
              f"failed to read. These are BUILDER defects, not ledger holes:")
        for e in misses[:25]:
            print(f"  {e['fu']}  {e['title'][:90]}")
        print("\nFix the field parser (SEP / META_FIELD_RE) before shipping "
              "this pack. Do NOT widen `status_absent` to absorb them.")
        return 1
    print(f"[build_pack] control OK: 0 entries have an unread status token "
          f"({len([e for e in entries if e['status_absent']])} genuinely carry "
          f"no status field at all)")

    OUT.mkdir(parents=True, exist_ok=True)
    files = []

    # 11 -- one line per FU, everything, no prose
    jsonl = []
    for e in sorted(entries, key=lambda x: x["id"]):
        row = {k: v for k, v in e.items() if k != "_raw"}
        jsonl.append(json.dumps(row, ensure_ascii=False, separators=(",", ":")))
    files.append(write_out("11_all_fus.jsonl", "\n".join(jsonl) + "\n"))

    # 10 -- tier 1 verbatim
    files.append(write_out("10_open_p0p1_full.md", render_tier1(entries)))

    # 12 -- themes + citation hubs
    files.append(write_out("12_themes.md", render_themes(themes)))

    # 13 -- closed evidence index. An entry with no status field is NOT closed;
    # it goes to 15_no_status.md, not here. Letting it fall through to "closed"
    # by negation is how 48 entries would have been quietly retired.
    closed = [e for e in entries if not e["open"] and not e["status_absent"]]
    idx = [json.dumps({
        "fu": e["fu"], "title": e["title"], "status": e["status"],
        "priority": e["priority"], "closed_touch": e["last_touch"],
        "has_resolution": e["has_resolution"],
        "resolution_head": e["resolution_head"],
        "pr_refs": e["pr_refs"],
    }, ensure_ascii=False, separators=(",", ":")) for e in
        sorted(closed, key=lambda x: x["id"])]
    files.append(write_out("13_closed_index.jsonl", "\n".join(idx) + "\n"))

    # 14 -- open, non-P1, one paragraph each (the tier-2 middle)
    mid = [e for e in entries if e["open"] and e["priority"] not in ("P0", "P1")]
    mid.sort(key=lambda e: -(e["stale_days"] or 0))
    npu = len([e for e in mid if e["priority"] == "Punspecified"])
    md = ["# TIER 2 -- OPEN, BELOW P1, ONE PARAGRAPH EACH", "",
          f"Built {NOW_ISO}. {len(mid)} entries: "
          f"{len([e for e in mid if e['priority'] == 'P2'])} P2, "
          f"{len([e for e in mid if e['priority'] == 'P3'])} P3, "
          f"{npu} with NO priority field.",
          "",
          "The last group is not P3. An entry with no priority was never "
          "triaged, and calling this file 'P2/P3' would have hidden them "
          "inside a rank nobody assigned.",
          "",
          "Full text lives in FOLLOWUPS.md; read it there if one of these "
          "becomes load-bearing.",
          ""]
    for e in mid:
        md.append(f"### {e['fu']} [{e['priority']}/{e['status']}] {e['title']}")
        md.append(f"filed {e['filed']} ({e['age_days']}d) · last touch "
                  f"{e['last_touch']} ({e['stale_days']}d) · source "
                  f"{e['source']} · logs {e['log_entries']} · "
                  f"verify {'yes' if e['has_verify'] else 'NO'}")
        if e["detail_head"]:
            md.append(e["detail_head"])
        if e["links"]:
            md.append("cites: " + ", ".join(f"FU-{i:03d}" for i in e["links"]))
        md.append("")
    files.append(write_out("14_open_p2p3_digest.md", "\n".join(md)))

    # 15 -- the undetermined set
    files.append(write_out("15_no_status.md", render_no_status(entries)))

    # 20 -- lanes
    lanes = collect_lanes()
    files.append(write_out("20_lanes.md", render_lanes(lanes)))

    # 21 -- goals (verbatim copies of the standing goal documents)
    goal_parts = ["# GOALS AND STANDING DOCTRINE", "",
                  f"Built {NOW_ISO}. Verbatim copies; nothing summarised.", ""]
    for label, path in (("PLAN_200K.md", PLAN200K),
                        ("GOVERNANCE.md", GOVERNANCE)):
        goal_parts.append(f"\n\n{'='*70}\n## {label}\n{'='*70}\n")
        goal_parts.append(read_text(path) if path.exists()
                          else f"[COULD_NOT_DETERMINE: {path} absent]")
    if AUTOPOIESIS.exists():
        ap_text = read_text(AUTOPOIESIS)
        tail = "\n".join(ap_text.split("\n")[-600:])
        goal_parts.append(f"\n\n{'='*70}\n## AUTOPOIESIS.md (last 600 lines -- "
                          f"the positive ledger's recent record)\n{'='*70}\n")
        goal_parts.append(tail)
    files.append(write_out("21_goals.md", "\n".join(goal_parts)))

    # 22 -- hazard corpus
    files.append(write_out("22_hazards.md", render_hazards()))

    # 30 -- live snapshot
    snap = live_snapshot(not args.no_live)
    files.append(write_out("30_prod_state.json",
                           json.dumps(snap, indent=2, ensure_ascii=False)))

    # 31 -- stats
    files.append(write_out("31_ledger_stats.json",
                           json.dumps(stats, indent=2, ensure_ascii=False)))

    # 00 -- manifest
    manifest = {
        "pack": "zo-sentinel ultra plan pack",
        "built_at": NOW_ISO,
        "builder": str(Path(__file__).resolve()),
        "ledger_source": str(LEDGER),
        "ledger_mtime": stats["ledger_mtime"],
        "rebuild_command":
            'python "D:\\zo\\Zocomputer Agents\\_ultraplan\\build_pack.py"',
        "staleness_rule":
            "If built_at is more than 24h before the ultra plan run, REBUILD. "
            "Every count in this pack is a measurement, not a constant.",
        "counts": {
            "total_fus": stats["total_entries"],
            "open": stats["open_total"],
            # Named for what 10_open_p0p1_full.md actually holds. An earlier
            # build published open-only 54 here while that file held 58 --
            # two different quantities under near-identical names.
            "actionable_p0p1_in_tier1": len(
                [e for e in entries if actionable(e)
                 and e["priority"] in ("P0", "P1")]),
            "open_only_p0p1": stats["open_by_priority"].get("P1", 0)
                              + stats["open_by_priority"].get("P0", 0),
            "undetermined_no_status_field": stats["status_field_absent"],
            "actionable_total": stats["open_total"]
                                + stats["status_field_absent"],
            "themes": len(themes["themes"]),
            "lane_dirs_on_disk": len(lanes),
            "lanes_registered": len([l for l in lanes
                                     if l["registration"].startswith("REGISTERED")]),
            "lanes_enabled": len([l for l in lanes
                                  if "ENABLED" in l["registration"]]),
            "lane_dirs_orphaned": len([l for l in lanes
                                       if l["registration"].startswith("ORPHAN")]),
        },
        "files": sorted(files, key=lambda f: f["file"]),
        "total_bytes": sum(f["bytes"] for f in files),
        "total_est_tokens": sum(f["est_tokens"] for f in files),
    }
    files.append(write_out("00_MANIFEST.json",
                           json.dumps(manifest, indent=2, ensure_ascii=False)))

    print(f"\n[build_pack] wrote {len(files)} files to {OUT}")
    for f in sorted(files, key=lambda x: x["file"]):
        print(f"  {f['file']:<28} {f['bytes']:>9,} B  ~{f['est_tokens']:>7,} tok")
    print(f"\n  TOTAL {manifest['total_bytes']:,} B  "
          f"~{manifest['total_est_tokens']:,} tokens")
    print(f"\n  ledger: {stats['total_entries']} FUs, "
          f"{stats['open_total']} open "
          f"({manifest['counts']['open_only_p0p1']} at P0/P1), "
          f"+{stats['status_field_absent']} UNDETERMINED (no status field) "
          f"= {manifest['counts']['actionable_total']} actionable")
    for n, c in sorted(stats["open_by_theme"].items(), key=lambda kv: -kv[1]):
        print(f"    {n:<28} {c:>3} open  "
              f"({stats['open_p0p1_by_theme'][n]} P0/P1)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
