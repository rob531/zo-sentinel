"""Diagnostic canary probe v2."""


def probe2() -> str:
    """Return a known-good string for build diagnostic."""
    return "goose-write-ok-2"


if __name__ == "__main__":
    print("PASS")
