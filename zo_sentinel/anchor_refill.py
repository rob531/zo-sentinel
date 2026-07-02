"""
anchor_refill.py -- the SELF-REFILLING ANCHOR: deterministic candidate mining
from the Graphify-indexed knowledge layer.

THE DISEASE (the recurring "anchor exhaustion = no novelty" class): the
architect's only "what to build" signal is live_gaps_map = PRODUCT_SPEC.md
candidate filenames minus disk. When a human-authored appendix is fully
realized, gaps = (none) and the architect either idles or -- worse, observed
live 2026-07-02 14:05 UTC -- burns its whole turn budget inventing
near-duplicate low-value proposals (mcp_risk_tier_{summary,comparison,
overview}_view). Every appendix refill so far has been a HUMAN point-in-time
act. This module closes the loop.

THE MECHANISM (deterministic -- no LLM, no network):
  When the live anchor runs LOW (missing candidates < threshold), mine the
  knowledge layer -- docs/DESIGN_*.md + SENTINEL_ROADMAP_v2*.md, the same
  documents Graphify indexes and design work (incl. mesh_memory-mirrored
  design notes) lands in -- for module filenames (*.py, *.html) that are:
    not on disk, not already spec candidates, not retired, not quarantined.
  Each mined name is appended to PRODUCT_SPEC_AUTO_ANCHOR.md as a standard
  `- directive candidate:` line carrying the SURROUNDING PARAGRAPH from the
  source doc as its description and a provenance stamp [auto-anchor:
  <source>#L<line>]. directive_knowledge_sources.load_product_spec() folds the
  auto-anchor file into the spec text, so the existing gaps-map extractor and
  the architect consume auto candidates through the exact same pipe as
  human-authored ones. Design docs written into the KL therefore BECOME
  directives when the anchor runs dry -- the embellishment loop, closed.

WHY A SEPARATE FILE (non-fragility): PRODUCT_SPEC.md stays human-owned; the
machine appends only to PRODUCT_SPEC_AUTO_ANCHOR.md. Deleting that one file
reverts every effect this module has ever had.

IDEMPOTENT / BOUNDED / GATED:
  - Re-runs are no-ops: a mined name already in the combined spec text (or on
    disk, retired, quarantined) is excluded, so nothing is ever appended twice.
  - Bounded: max_new per pass (default 5) and only when missing < threshold
    (default 5) -- a full anchor is never touched.
  - Gated, default OFF: env ZO_ANCHOR_REFILL=1 or sentinel file
    directives/.anchor_refill_on containing "1" (read fresh; flip live).
  - Fail-open: run_refill never raises; any fault returns stats and leaves
    every file untouched.

Pure stdlib. No import-time side effects. Fully path-parameterized for tests.
"""
from __future__ import annotations

import datetime
import logging
import os
import re
from pathlib import Path
from typing import List, Optional, Set

log = logging.getLogger("anchor_refill")

SENTINEL_NAME = ".anchor_refill_on"
ENV_FLAG = "ZO_ANCHOR_REFILL"
AUTO_ANCHOR_NAME = "PRODUCT_SPEC_AUTO_ANCHOR.md"
DEFAULT_THRESHOLD = int(os.environ.get("ZO_ANCHOR_THRESHOLD", "5"))
DEFAULT_MAX_NEW = int(os.environ.get("ZO_ANCHOR_MAX_NEW", "5"))

# Same shape as directive_knowledge_sources._CANDIDATE_FILENAME, restricted to
# BUILD targets (.py/.html -- .md is documentation, not a directive target).
# Keep in sync with the extractor; drift here means mined names the gaps map
# cannot see.
_FILENAME_RX = re.compile(r"\b([a-z][a-z0-9_]{2,40}\.(?:py|html))\b")

# Mirrors the extractor's trigger vocabulary (directive_knowledge_sources
# _spec_candidate_files). A mined line is only *counted as already-a-candidate*
# if it sits near one of these flags -- same rule the gaps map applies.
_CANDIDATE_FLAGS = ("not yet", "directive candidate", "candidate:",
                    "candidates:", "propose directives", "dormant")

# Names that are infrastructure/noise, never build targets.
_EXCLUDE_PREFIXES = ("test_", "conftest")


def enabled(directives_root) -> bool:
    """Read-fresh gate: env ZO_ANCHOR_REFILL or directives/.anchor_refill_on."""
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


def spec_candidate_files(spec_text: str) -> List[str]:
    """The gaps-map extractor's rule, reproduced exactly (filenames near a
    candidate flag, this line + next 3). Includes .md like the original so the
    'already a candidate' set is a superset -- never re-mine what the map sees."""
    rx = re.compile(r"\b([a-z][a-z0-9_]{2,40}\.(?:py|html|md))\b")
    out: List[str] = []
    lines = spec_text.splitlines()
    for i, ln in enumerate(lines):
        low = ln.lower()
        if any(flag in low for flag in _CANDIDATE_FLAGS):
            window = "\n".join(lines[i:i + 4])
            for m in rx.finditer(window):
                if m.group(1) not in out:
                    out.append(m.group(1))
    return out


def _disk_names(sentinel_dir: Path) -> Set[str]:
    names: Set[str] = set()
    try:
        for p in sentinel_dir.iterdir():
            if p.is_file():
                names.add(p.name)
    except Exception:
        pass
    return names


def _terminal_stems(directives_root: Path, quarantine_dir: Optional[Path]) -> Set[str]:
    """Task/id stems of retired + quarantined directives. A mined module name
    whose stem appears inside any terminal stem is excluded -- the builder
    already gave up on (or the janitor retired) that work; re-anchoring it
    would recreate the churn the janitor just ended."""
    stems: Set[str] = set()
    try:
        retired = directives_root / "retired"
        if retired.is_dir():
            for p in retired.rglob("*.json"):
                stems.add(p.stem.lower())
    except Exception:
        pass
    try:
        if quarantine_dir and Path(quarantine_dir).is_dir():
            for p in Path(quarantine_dir).glob("*.failed.json"):
                stems.add(p.name[:-len(".failed.json")].lower())
    except Exception:
        pass
    return stems


def _excluded_by_terminal(name: str, terminal_stems: Set[str]) -> bool:
    stem = Path(name).stem.lower()
    return any(stem in t or t in stem for t in terminal_stems if t)


def _paragraph_around(lines: List[str], idx: int, cap: int = 420) -> str:
    """The bullet/paragraph containing line idx, flattened + capped -- the
    mined candidate's description, straight from the KL doc (provenance-true,
    no synthesis)."""
    start = idx
    while start > 0 and lines[start - 1].strip() != "":
        start -= 1
    end = idx
    while end + 1 < len(lines) and lines[end + 1].strip() != "":
        end += 1
    text = " ".join(ln.strip().lstrip("-*# ").strip()
                    for ln in lines[start:end + 1] if ln.strip())
    text = re.sub(r"\s+", " ", text).strip()
    return text[:cap]


def mine_candidates(source_paths: List[Path], exclude: Set[str],
                    terminal_stems: Set[str]) -> List[dict]:
    """Deterministically mine KL docs for new build-target filenames.
    Returns [{file, desc, source, line}] in document order, de-duplicated."""
    found: List[dict] = []
    seen: Set[str] = set()
    for src in source_paths:
        try:
            lines = Path(src).read_text(encoding="utf-8",
                                        errors="ignore").splitlines()
        except Exception:
            continue
        for i, ln in enumerate(lines):
            for m in _FILENAME_RX.finditer(ln):
                name = m.group(1)
                if (name in seen or name in exclude
                        or name.startswith(_EXCLUDE_PREFIXES)
                        or _excluded_by_terminal(name, terminal_stems)):
                    continue
                seen.add(name)
                found.append({"file": name,
                              "desc": _paragraph_around(lines, i),
                              "source": Path(src).name,
                              "line": i + 1})
    return found


def run_refill(sentinel_dir, threshold: int = DEFAULT_THRESHOLD,
               max_new: int = DEFAULT_MAX_NEW,
               sources: Optional[List[Path]] = None,
               quarantine_dir: Optional[Path] = None,
               now: Optional[str] = None) -> dict:
    """One refill pass. Never raises. Returns stats.

    No-op unless the LIVE anchor is low: missing = spec candidates (human spec
    + auto anchor) minus disk; missing >= threshold => untouched.
    """
    stats = {"missing": None, "mined": 0, "appended": 0, "reason": "",
             "files": []}
    try:
        root = Path(sentinel_dir)
        spec_path = root / "PRODUCT_SPEC.md"
        auto_path = root / AUTO_ANCHOR_NAME
        directives_root = root / "directives"
        if quarantine_dir is None:
            quarantine_dir = Path(os.environ.get(
                "ZO_DURABLE_QUARANTINE_DIR",
                "/home/workspace/zo_sentinel_state/quarantine"))

        spec_text = ""
        try:
            spec_text = spec_path.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            stats["reason"] = "spec_unreadable"
            return stats
        auto_text = ""
        if auto_path.is_file():
            auto_text = auto_path.read_text(encoding="utf-8", errors="ignore")
        combined = spec_text + "\n" + auto_text

        candidates = spec_candidate_files(combined)
        disk = _disk_names(root)
        missing = [c for c in candidates if c not in disk]
        stats["missing"] = len(missing)
        if len(missing) >= threshold:
            stats["reason"] = "anchor_sufficient"
            return stats

        if sources is None:
            sources = sorted((root / "docs").glob("DESIGN_*.md"))
            for extra in ("SENTINEL_ROADMAP_v2.md",
                          "SENTINEL_ROADMAP_v2_addendum.md"):
                p = root / extra
                if p.is_file():
                    sources.append(p)

        exclude = set(candidates) | disk
        terminal = _terminal_stems(directives_root, quarantine_dir)
        mined = mine_candidates([Path(s) for s in sources], exclude, terminal)
        stats["mined"] = len(mined)
        if not mined:
            stats["reason"] = "nothing_new_in_kl"
            return stats

        batch = mined[:max_new]
        ts = now or datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%MZ")
        block = [
            "",
            f"## Auto-anchor refill {ts} (directive candidates, NOT YET BUILT)",
            "",
            "*Mined deterministically from the Graphify KL (docs/DESIGN_*.md, "
            "roadmap) by zo_sentinel/anchor_refill.py because the live anchor "
            f"ran low (missing={len(missing)} < threshold={threshold}). "
            "Provenance per line. Delete this file to revert all auto "
            "candidates.*",
            "",
        ]
        for c in batch:
            block.append(f"- directive candidate: `{c['file']}` -- {c['desc']} "
                         f"[auto-anchor: {c['source']}#L{c['line']}]")
        block.append("")
        with auto_path.open("a", encoding="utf-8") as f:
            f.write("\n".join(block))
        stats["appended"] = len(batch)
        stats["files"] = [c["file"] for c in batch]
        stats["reason"] = "refilled"
        log.info("anchor refill: +%d candidates (%s)", len(batch),
                 ", ".join(stats["files"]))
        return stats
    except Exception as e:  # belt: a refill fault must never break generation
        stats["reason"] = f"error: {type(e).__name__}: {e}"
        return stats
