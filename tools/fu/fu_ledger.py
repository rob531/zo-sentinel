#!/usr/bin/env python3
"""Shared parse/serialise model for FOLLOWUPS.md.

Single source of truth for the FU entry shape. `ledger_lint.py` and
`fu_verify.py` BOTH import this rather than re-implementing the regexes --
a deliberate response to the scar where a helper shipped with an owner the
caller had never heard of, and to the scar where two components disagreed
about a schema because each parsed it independently.

Schema (v2, 2026-07-28):

    ### FU-NNN | <short imperative title>
    - date: YYYY-MM-DD - source: <task> - status: <st> - priority: Pn
    - class: defect | directive | learning
    - detail: <what and why>
    - verify: `<shell command; exit 0 IFF this FU is fixed>`
    - verify_seen_red: <ISO8601 ts> | NEVER
    - log:
    - resolution:

Why `verify:` exists
    Before v2 every field was prose, so the only thing a validator could
    enforce was SHAPE. That is why the 2026-07-28 lint "fixed" FU-114 by
    appending an empty `- resolution:` key and closed nothing. A ledger of
    148 prose entries cannot be acted on by an agent; a ledger of 148
    executable predicates is a regression suite.

Why `verify_seen_red:` exists
    An assertion never observed failing is not evidence. The goose-canary
    passed for days while driving a transport the mesh never uses. A verify
    that has never been RED may be passing because it tests nothing, so a
    green from such a predicate is RECORDED but never acted on.

Why `class:` exists
    The ledger conflates three object types. Only DEFECTS can carry a
    verify. Filing a directive ("STEER: files -> services") or a learning
    ("never rationalise a merge") as a P1 defect is what makes the open
    count meaningless. Class makes the distinction checkable.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional

HEAD_RE = re.compile(r"^### FU-(\d+)\b(?:\s*\|\s*(.*))?$")
KEY_RE = re.compile(r"^- ([a-z][a-z_ ]*):\s?(.*)$")

# The `- date:` line packs 4 fields separated by any of ' - ', ' · ', ' • '.
FIELD_SEP_RE = re.compile(r"\s+[·•]\s+|\s+-\s+")

VALID_CLASS = ("defect", "directive", "learning")
VALID_STATUS = ("open", "in-progress", "pr-open", "watch", "resolved", "done", "wontfix")
NO_VERIFY = "NONE"
NEVER_RED = "NEVER"

# A verify runs unattended on a cadence. It is a PROBE, never an actor.
# These tokens are refused outright rather than sandboxed, because the cost of
# a false negative here is destroyed state or a burned budget.
FORBIDDEN = (
    "rm ", "rm -", "mv ", "dd ", "mkfs", "shutdown", "reboot", "kill ", "pkill",
    "drop table", "drop database", "truncate", "delete from", "update ", "insert into",
    "alter table", "git push", "git reset --hard", "git clean", "destroy",
    "terminate", "vast ", "runpod", "sky launch", "fly deploy", "flyctl deploy",
    ">>", "tee ", "chmod ", "chown ", "--force", "sudo ",
)


def _forbidden_matcher(tok: str):
    """Compile the matcher for one forbidden token.

    FU-158 / 2026-08-02: a BARE SUBSTRING TEST IS WRONG, and it had been wrong
    since the list was written. `"dd "` matched inside `git worktree add
    --detach`, so a read-only `pytest` predicate was reported E9 "unsafe --
    contains 'dd'" on every lint run for five days. The same trap is latent in
    `"rm "` (matches `perform `, `platform `), `"mv "`, `"kill "` and
    `"update "`.

    A checker that cries wolf on a safe predicate does not fail loudly -- it
    gets muted, and then it is not protecting anything. So: tokens that START
    with a word character require a word boundary in front. Tokens that start
    with punctuation (`">>"`, `"--force"`) keep plain substring matching,
    because a boundary rule would make THOSE weaker, not stronger, and the cost
    of a false negative on a redirect is destroyed state.
    """
    if tok[:1].isalnum():
        return re.compile(r"(?<![A-Za-z0-9_])" + re.escape(tok))
    return re.compile(re.escape(tok))


_FORBIDDEN_MATCHERS = tuple((tok, _forbidden_matcher(tok)) for tok in FORBIDDEN)
# NOTE: blanket "POST" was in this list and was removed. The documented READ
# path for this fleet is a POST to the write-service /query bus, so refusing
# POST would refuse almost every useful database predicate. Mutation is caught
# precisely, by SQL verb (delete from / update / insert into / drop / alter),
# which is what actually distinguishes a read from a write.
# NOTE: `-f /` was in this list and was removed after it refused
# `test -f /path`, a read-only existence check. A guard that blocks the
# legitimate case is not a stricter guard, it is a broken one -- it pushes
# authors toward writing no predicate at all, which is the failure this
# whole mechanism exists to prevent. `rm ` / `rm -` still catch `rm -f /`.


@dataclass
class FU:
    num: str
    title: str
    start: int                      # index of the `### FU-NNN` line
    end: int                        # exclusive
    keys: Dict[str, int] = field(default_factory=dict)   # key -> line index
    vals: Dict[str, str] = field(default_factory=dict)   # key -> raw value
    date: str = ""
    source: str = ""
    status_raw: str = ""
    priority: str = ""

    @property
    def id(self) -> str:
        return "FU-%s" % self.num

    @property
    def status(self) -> str:
        """First token of the status field, lowercased.

        The raw field is prose in at least one entry ("resolved (code merged
        ... awaiting prod deploy)"). Status is an ENUM and every consumer
        must agree on it, so normalise here rather than in each caller.
        """
        return (self.status_raw or "").strip().split()[0].lower() if self.status_raw else ""

    @property
    def status_is_clean(self) -> bool:
        return self.status_raw.strip() in VALID_STATUS

    @property
    def fu_class(self) -> str:
        c = (self.vals.get("class") or "").strip().lower()
        return c if c in VALID_CLASS else ""

    @property
    def verify_cmd(self) -> Optional[str]:
        """The runnable command, or None if absent / explicitly NONE.

        The stored form is:  `<command>`  # why this is the right predicate

        A previous version did `raw.strip("`")`, which only strips a trailing
        backtick when it is the LAST character. With a trailing comment the
        closing backtick survived and the whole comment was handed to the
        shell -- which on cmd.exe returned 0, reporting a FALSE GREEN for a
        command (`flyctl auth whoami`) that genuinely exits 1. Caught on the
        first live sweep, and only because `verify_seen_red` refused to act on
        a green from an unproven predicate. Extract the fenced command
        explicitly; never hand the annotation to a shell.
        """
        raw = (self.vals.get("verify") or "").strip()
        if not raw or raw.upper().startswith(NO_VERIFY):
            return None
        m = re.search(r"`(.+?)`", raw, re.S)
        if m:
            return m.group(1).strip()
        return raw.split("  #")[0].strip()

    @property
    def verify_is_none(self) -> bool:
        raw = (self.vals.get("verify") or "").strip()
        return raw.upper().startswith(NO_VERIFY)

    @property
    def seen_red(self) -> Optional[str]:
        """ISO timestamp at which this verify was observed RED, else None."""
        raw = (self.vals.get("verify_seen_red") or "").strip()
        if not raw or raw.upper().startswith(NEVER_RED):
            return None
        return raw

    def is_open(self) -> bool:
        return self.status in ("open", "in-progress", "pr-open", "watch")

    def unsafe_reason(self) -> Optional[str]:
        """Non-None if the verify command must never be executed."""
        cmd = self.verify_cmd
        if not cmd:
            return None
        low = cmd.lower()
        for tok, pat in _FORBIDDEN_MATCHERS:
            if pat.search(low):
                return "contains forbidden token %r -- a verify is a read-only probe" % tok.strip()
        return None


def parse(lines: List[str]) -> List[FU]:
    heads = [(i, m.group(1), (m.group(2) or "").strip())
             for i, l in enumerate(lines) for m in [HEAD_RE.match(l)] if m]
    out: List[FU] = []
    for n, (idx, num, title) in enumerate(heads):
        end = heads[n + 1][0] if n + 1 < len(heads) else len(lines)
        fu = FU(num=num, title=title, start=idx, end=end)
        for j in range(idx + 1, end):
            m = KEY_RE.match(lines[j])
            if not m:
                continue
            key = m.group(1).strip().replace(" ", "_")
            if key in fu.keys:          # first occurrence wins
                continue
            fu.keys[key] = j
            fu.vals[key] = m.group(2)
        if "date" in fu.keys:
            for part in FIELD_SEP_RE.split(lines[fu.keys["date"]]):
                p = part.strip().lstrip("- ").strip()
                for name in ("date", "source", "status", "priority"):
                    pref = name + ":"
                    if p.lower().startswith(pref):
                        attr = "status_raw" if name == "status" else name
                        setattr(fu, attr, p[len(pref):].strip())
        out.append(fu)
    return out


def line_terminator(lines: List[str]) -> str:
    """Return the terminator the caller's `lines` carry ("" if unterminated).

    The writers here were all written against `text.splitlines()` -- lines with
    NO terminator, re-joined with "\\n". A caller that instead passes
    `splitlines(keepends=True)` used to have every inserted string GLUED to the
    line below it, because the inserted string had no "\\n" of its own and
    `"".join(...)` therefore ran the two together.

    That is not a hypothetical: on 2026-08-02 `append_log` called with keepends
    lines silently swallowed FU-054's `- resolution:` key into the tail of the
    new log bullet. The ledger still *parsed*, so nothing went red -- the exact
    "a check never observed RED is not evidence" shape from HARNESS_DOCTRINE.
    Detect the convention rather than assume it, so BOTH callers are correct.
    """
    for ln in lines:
        if ln.endswith("\r\n"):
            return "\r\n"
        if ln.endswith("\n"):
            return "\n"
    return ""


# ---------------------------------------------------------------- call shape
# ADDED 2026-09-03 (improvement-loop cycle-0065) for the friction family
# `sanctioned-writer-api-shape`: 5 bites across 5 lanes in the trailing 7d,
# first bitten 2026-08-30. Three of the six recorded rows are THIS exact call.
#
# THE DEFECT. Every lane prompt on this tower names `fu_ledger.append_log` as
# "the sanctioned writer". It reads like a writer and it is not: it is a PURE
# function over a parsed line list that neither opens nor writes the ledger.
# Lanes therefore call `append_log(fu, text)` and get
#     TypeError: append_log() missing 1 required positional argument: 'text'
# which names the ARITY and nothing else -- not the real shape, not the host
# dance around it, not the one-call CLI that already does all of it. Recorded
# cost of re-deriving that from the bare TypeError: 3-6 minutes, per bite,
# per lane, five times in the last seven days.
#
# THIS IS NOT A NEW GATE (HARNESS_DOCTRINE R7, recovery over restriction). It
# refuses nothing that used to work -- the guessed call was ALREADY a
# TypeError. It changes only what that TypeError SAYS, at the exact moment and
# on the exact surface where the bitten lane is standing. A hazard note in a
# prompt was tried first and is the reason this family has a name at all.
_MISSING = object()

_SHAPE_HINT = r"""
fu_ledger.%(fn)s(lines, fu, %(rest)s) is a PURE FUNCTION over a parsed line
list. It does NOT open, read, write or back up the ledger -- the caller owns
the file. You called it as %(fn)s(<%(got0)s>, ...), which is the shape the
lane prompts imply and which no version of this module has ever had.

ONE-CALL FIX -- prefer this. It does the whole dance (backup, parse, append,
binary write, re-parse verify) and is idempotent via --if-absent:

  python "D:\zo\Zocomputer Agents\_tools\fu_append_log.py" --fu NNN --message "<one line>" --if-absent "<unique substring from this write>"

  Add --dry-run first. Exit 0 = bullet present in the re-parsed ledger;
  1 = refused (bad args / unknown FU); 2 = WROTE BUT COULD NOT VERIFY,
  treat as a failed write and restore the backup it printed.

IN-PROCESS FIX, if you genuinely need the pure function:

  raw   = open(LEDGER, encoding="utf-8", newline="").read()
  lines = raw.splitlines(keepends=True)
  fu    = {str(f.num).lstrip("0"): f for f in parse(lines)}["NNN"]
  append_log(lines, fu, text)              # <-- lines FIRST, then fu, then text
  open(LEDGER, "wb").write("".join(lines).encode("utf-8"))
  # then assert the LF count GREW and the crlf/lf ratio class did not move

Two more shapes in this same family, so you do not pay for them separately:
  * FU.num is a STRING with leading zeros ('035'), never an int. Key it as
    str(f.num).lstrip("0"); `f.num == 35` can only ever be False, and an
    identity check that is always False reads as "FU-035 does not exist".
  * The heading form is `### FU-NNN | title` -- three hashes, a pipe, no
    colon and no double dash. HEAD_RE is the contract; a heading that misses
    it is INVISIBLE to fu_verify.py while ledger_lint.py still calls the file
    clean.
"""


def _shape_error(fn, rest, got0):
    return TypeError(_SHAPE_HINT % {
        "fn": fn, "rest": rest, "got0": type(got0).__name__,
    })


def insert_key(lines, fu=_MISSING, key=_MISSING, value=_MISSING,
               before="log") -> int:
    """Insert `- key: value` into an entry. Body lives in _insert_key.

    Guard added 2026-09-03 (cycle-0065): same call-shape family as append_log,
    censused and cured in the SAME commit rather than one door of two.
    """
    if value is _MISSING or not isinstance(lines, list):
        raise _shape_error("insert_key", "key, value", lines)
    return _insert_key(lines, fu, key, value, before)


def _insert_key(lines: List[str], fu: FU, key: str, value: str, before: str = "log") -> int:
    """Insert `- key: value` into an entry, preferring a slot before `before`.

    Returns the index written. Callers MUST re-parse afterwards; indices in
    previously-parsed FU objects are invalidated by this call.

    Accepts `lines` with or without line terminators (see `line_terminator`).
    """
    eol = line_terminator(lines)
    line = "- %s: %s" % (key, value) if value else "- %s:" % key
    line += eol
    if key in fu.keys:
        lines[fu.keys[key]] = line
        return fu.keys[key]
    pos = fu.keys.get(before)
    if pos is None:
        pos = fu.end
        while pos - 1 > fu.start and lines[pos - 1].strip() in ("", "---"):
            pos -= 1
    lines.insert(pos, line)
    return pos


def _is_wrapped_log_line(line: str) -> bool:
    """True for a continuation line of the log bullet ABOVE it.

    Log entries are `  - <dated text>` bullets whose prose routinely wraps onto
    lines indented past the bullet marker. Those wrapped lines belong to their
    bullet, so an append has to step over them as well as over the bullet heads
    -- scanning only for `  - ` stops at the first wrapped line and inserts the
    new entry INSIDE the previous one, silently re-parenting its prose.
    """
    return line.startswith("    ") and line.strip() != ""


def append_log(lines, fu=_MISSING, text=_MISSING) -> int:
    """Append a dated bullet under `- log:`. Body lives in _append_log.

    Guard added 2026-09-03 (cycle-0065): the shape the fleet guesses,
    append_log(fu, text), now raises a TypeError that names the correct
    signature and the one-call CLI instead of only the missing arity.
    """
    if text is _MISSING or not isinstance(lines, list):
        raise _shape_error("append_log", "text", lines)
    return _append_log(lines, fu, text)


def _append_log(lines: List[str], fu: FU, text: str) -> int:
    """Append a dated bullet under `- log:`, creating the key if needed.

    Accepts `lines` with or without line terminators (see `line_terminator`).
    """
    eol = line_terminator(lines)
    bullet = "  - %s%s" % (text, eol)
    if "log" in fu.keys:
        pos = fu.keys["log"] + 1
        while pos < fu.end and (
            lines[pos].startswith("  - ") or _is_wrapped_log_line(lines[pos])
        ):
            pos += 1
        lines.insert(pos, bullet)
        return pos
    pos = fu.keys.get("resolution", fu.end)
    lines.insert(pos, "- log:" + eol)
    lines.insert(pos + 1, bullet)
    return pos + 1
