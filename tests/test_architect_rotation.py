"""Unit tests for the architect capable-rung rotation policy (pure function)."""
from zo_sentinel.sentinel_directive_generator_goose import _rotated_model

ROT = ["zo-ladder-cerebras", "zo-ladder-nvidia", "zo-ladder-mistral", "zo-ladder-groq"]


def test_no_rotation_below_threshold():
    assert _rotated_model("zo-ladder-cerebras", 0, 2, ROT) == "zo-ladder-cerebras"
    assert _rotated_model("zo-ladder-cerebras", 1, 2, ROT) == "zo-ladder-cerebras"


def test_rotates_at_threshold():
    assert _rotated_model("zo-ladder-cerebras", 2, 2, ROT) == "zo-ladder-nvidia"
    assert _rotated_model("zo-ladder-cerebras", 3, 2, ROT) == "zo-ladder-nvidia"


def test_walks_further_each_window():
    assert _rotated_model("zo-ladder-cerebras", 4, 2, ROT) == "zo-ladder-mistral"
    assert _rotated_model("zo-ladder-cerebras", 6, 2, ROT) == "zo-ladder-groq"
    # wraps back around to home
    assert _rotated_model("zo-ladder-cerebras", 8, 2, ROT) == "zo-ladder-cerebras"


def test_disabled_when_rot_after_zero():
    assert _rotated_model("zo-ladder-cerebras", 99, 0, ROT) == "zo-ladder-cerebras"


def test_unknown_home_is_prepended():
    got = _rotated_model("custom-rung", 2, 2, ROT)
    assert got == "zo-ladder-cerebras"  # first step off the prepended home


def test_empty_rotation_noop():
    assert _rotated_model("zo-ladder-cerebras", 10, 2, []) == "zo-ladder-cerebras"
