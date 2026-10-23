"""Gap Register IDs are unique, and every queue entry names a row that exists.

2026-10-03: LOCO_CHAIRMAN.md carried GR-11/12/13 twice -- the 2026-08-29
addendum (graphify, bus mirror, landing chain) and the 2026-10-02 alerting rows
in section 6.1. QUEUE.md pointed at one meaning, CHECKPOINT.md at the other.
Two gaps under one name is GC-8: a grade claimed for one is read as the
other's. The addendum rows became GR-16/17/18; this test keeps it that way.
"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ROW = re.compile(r"^\|\s*(GR-\d+)\b", re.M)
QUEUE_ENTRY = re.compile(r"^##\s+\d+[a-z]?\.\s+(GR-\d+)\b", re.M)


def register_ids(text):
    return ROW.findall(text)


def duplicates(ids):
    return sorted({i for i in ids if ids.count(i) > 1})


def test_register_ids_unique():
    ids = register_ids((ROOT / "LOCO_CHAIRMAN.md").read_text(encoding="utf-8"))
    assert len(ids) >= 15, "register parse found %d rows -- parser broken, not clean" % len(ids)
    assert not duplicates(ids), "duplicate Gap Register IDs: %s" % duplicates(ids)


def test_queue_entries_name_real_rows():
    ids = set(register_ids((ROOT / "LOCO_CHAIRMAN.md").read_text(encoding="utf-8")))
    queued = QUEUE_ENTRY.findall((ROOT / "chairman" / "QUEUE.md").read_text(encoding="utf-8"))
    assert queued, "queue parse found no GR entries -- parser broken, not clean"
    missing = sorted(set(queued) - ids)
    assert not missing, "QUEUE.md names rows the register does not have: %s" % missing


def test_negative_control_duplicate_is_caught():
    text = ("| GR-1 | a | GC-1 | C0 | C4 | x | d |\n"
            "| GR-2 | b | GC-1 | C0 | C4 | x | d |\n"
            "| GR-1 | c | GC-8 | C0 | C4 | x | d |\n")
    assert duplicates(register_ids(text)) == ["GR-1"]
    assert not duplicates(register_ids(text.replace("| GR-1 | c", "| GR-3 | c")))


if __name__ == "__main__":
    for t in (test_register_ids_unique, test_queue_entries_name_real_rows,
              test_negative_control_duplicate_is_caught):
        t()
    print("ok")
