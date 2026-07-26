"""FU-105 regression: `git ls-tree -r -l` field indices.

Run 20260726-014732 pushed a perfect bundle -- adapter_model.safetensors, 29,528,024
bytes, confirmed present on the remote branch -- and the post-push verify aborted it
anyway, at $0 but also at zero yield. FU-093's index math assumed the FOUR-field
`ls-tree -r` shape; `-l` inserts an object SIZE column and makes it FIVE:

    100644 blob <sha> 29528024\tscore_transfer/adapter/adapter_model.safetensors
     f[0]   f[1] f[2]   f[3]      f[4]

So it read f[3] (the size) as the path and f[2] (the sha) as the size. Two silent
consequences, opposite in sign:
  * the remote verify could never return ok  -> EVERY bundle aborted;
  * the 133-byte LFS-pointer guard could never fire -> a DECORATIVE gate, which is
    precisely the class FU-093 existed to kill.

TREE below is real captured output, byte-for-byte, from the run that was wrongly
aborted -- not a hand-written fixture. Synthetic uniform fixtures are what let the
original bug through: a fixture written by the same hand that wrote the parser
encodes the same misunderstanding.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

MODULE_PATH = Path(__file__).resolve().parents[1] / "tools" / "rescore" / "weekly_rescore.py"

# real `git ls-tree -r -l HEAD score_transfer/adapter`, run 20260726-014732
TREE = (
    "100644 blob 6405038a5648fac565f7d546044d9ab5f06e4323    5089\tscore_transfer/adapter/README.md\n"
    "100644 blob 41b3d27320dc5b26317f706a7ad0d7574667ea6b     752\tscore_transfer/adapter/adapter_config.json\n"
    "100644 blob 934805596ff44d345756752981cd9b11ef1eba6c 29528024\tscore_transfer/adapter/adapter_model.safetensors\n"
    "100644 blob 0c655612c61329c53a2e84b3b3990b93a6348bcc  267086\tscore_transfer/adapter/heads_state_dict.pt\n"
)


def _load():
    spec = importlib.util.spec_from_file_location("weekly_rescore_fu105", MODULE_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_reads_the_size_column_not_the_sha():
    """The exact bundle that was wrongly aborted must now verify."""
    assert _load().adapter_blob_size(TREE) == 29_528_024


def test_absent_adapter_is_none_not_a_crash():
    without = "\n".join(l for l in TREE.splitlines() if "safetensors" not in l)
    assert _load().adapter_blob_size(without) is None


def test_lfs_pointer_stub_is_still_catchable():
    """The 133-byte class must be DETECTED, not silently skipped (FU-093's purpose)."""
    stub = (
        "100644 blob 934805596ff44d345756752981cd9b11ef1eba6c     133"
        "\tscore_transfer/adapter/adapter_model.safetensors\n"
    )
    size = _load().adapter_blob_size(stub)
    assert size == 133
    assert size < 1_000_000


def test_empty_and_malformed_do_not_raise():
    m = _load()
    assert m.adapter_blob_size("") is None
    assert m.adapter_blob_size("garbage\nmore garbage") is None
    # a tree entry (no size, `-` in the size column) must not blow up int()
    assert m.adapter_blob_size("040000 tree abc123       -\tscore_transfer/adapter") is None
