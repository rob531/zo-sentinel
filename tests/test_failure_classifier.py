"""failure_classifier: each recurring class's signature maps to its bucket; healthy
lines map to None; tally aggregates."""
import failure_classifier as F


def test_each_class_signature():
    cases = {
        "ALERT path-drift: pending-dir X != goose pending": "path_drift",
        "proposed_depth 0 -> 0 (+0)": "novelty_starvation",
        "skip already-resolved gen_x done=True": "novelty_starvation",
        "error: 429 rate-limited (backoff exhausted)": "capacity_429",
        "Token Plan usage limit reached": "capacity_429",
        "self-hydrate keys timed out after 30s": "key_hydration",
        "RcGeminiAPIKey still unresolved": "key_hydration",
        "rejected: output_file_is_sane admin_admin_ui.py": "dup_poison",
        "no-op: artifact already on base (nothing to commit)": "publisher_noop_cap",
        "daily cap 8 reached": "publisher_noop_cap",
        "ghost-guard: success but output missing": "ghost_build",
        "Heartbeat failed ... port=8772 ... timed out": "write_service",
        "!! :8781 registry_api failed (000000)": "bootstrap_service",
    }
    for line, expected in cases.items():
        assert F.classify_line(line) == expected, (line, F.classify_line(line))


def test_healthy_lines_are_none():
    for ln in ["POST /v1/chat/completions HTTP/1.1 200 OK",
               "Cycle 14 complete, sleeping 60s",
               "promoter heartbeat: alive"]:
        assert F.classify_line(ln) is None


def test_tally_counts_and_examples():
    lines = ["429 rate-limited", "another 429 here", "nothing to commit", "200 OK"]
    counts, ex = F.tally(lines)
    assert counts["capacity_429"] == 2 and counts["publisher_noop_cap"] == 1
    assert "commit" in ex["publisher_noop_cap"]
