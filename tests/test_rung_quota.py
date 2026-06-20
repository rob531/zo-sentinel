"""rung_quota: header parsing, retry parsing, availability, park -- mostly pure."""
import time
import rung_quota as Q


def test_parse_headers_all_providers():
    assert Q.parse_headers({"x-ratelimit-remaining-requests-day": "2399",
                            "x-ratelimit-limit-requests-day": "2400"})["fraction"] > 0.99  # cerebras
    assert Q.parse_headers({"x-ratelimit-remaining-requests": "5",
                            "x-ratelimit-limit-requests": "1000"})["remaining"] == 5      # groq
    assert Q.parse_headers({"x-ratelimit-remaining-req-minute": "124",
                            "x-ratelimit-limit-req-minute": "125"})["limit"] == 125        # mistral
    assert Q.parse_headers({"anthropic-ratelimit-requests-remaining": "3999",
                            "anthropic-ratelimit-requests-limit": "4000"})["remaining"] == 3999
    assert Q.parse_headers({"content-type": "application/json"}) == {}                      # nvidia/gemini


def test_parse_retry_forms():
    assert Q._parse_retry("12") == 12
    assert Q._parse_retry("1.2s") == 1.2
    assert abs(Q._parse_retry("220ms") - 0.22) < 1e-9
    assert Q._parse_retry("2m") == 120
    assert Q._parse_retry(None) is None


def test_entry_available_logic():
    now = 1000.0
    assert Q._entry_available(None, now, 0.08)[0] is True
    assert Q._entry_available({"park_until": now + 30}, now, 0.08)[0] is False
    assert Q._entry_available({"fraction": 0.02}, now, 0.08)[0] is False
    assert Q._entry_available({"fraction": 0.5}, now, 0.08)[0] is True


def test_record_park_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr(Q, "QUOTA_FILE", tmp_path / "q.json")
    # 429 with retry-after parks it
    Q.record("m1", {"x-ratelimit-remaining-requests": "0", "x-ratelimit-limit-requests": "100"},
             status=429, retry_after="2s")
    ok, why = Q.available("m1")
    assert ok is False and "parked" in why
    # healthy capture is available
    Q.record("m2", {"x-ratelimit-remaining-requests": "900", "x-ratelimit-limit-requests": "1000"})
    assert Q.available("m2")[0] is True
    # explicit park expires
    Q.park("m3", seconds=0.01); time.sleep(0.05)
    assert Q.available("m3")[0] is True
    # near-empty gauge is skipped
    Q.record("m4", {"x-ratelimit-remaining-requests": "3", "x-ratelimit-limit-requests": "1000"})
    assert Q.available("m4")[0] is False
