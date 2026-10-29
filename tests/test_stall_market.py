from hoyo_analyzer.stall_market import StallVisitTracker


def key(seed: int) -> bytes:
    return bytes([seed] * 32)


def test_direct_market_switch_emits_once_but_first_visit_uses_panel_opened():
    tracker = StallVisitTracker()
    assert tracker.update(key(0), 100, 1) == []
    assert tracker.update(key(0), 200, 2) == []
    assert tracker.update(key(0), 300, 3) == []
    assert tracker.update(key(255), 400, 4) == []
    events = tracker.update(key(255), 500, 5)
    assert [event.type for event in events] == ["market_stall_changed"]
    assert events[0].payload["panel_type"] == "stall_other_buy"
    assert tracker.update(key(255), 600, 6) == []


def test_market_visit_hash_tolerates_small_capture_noise_and_resets_after_close():
    tracker = StallVisitTracker()
    baseline = bytes(32)
    noisy = bytes([0b00000001, *([0] * 31)])
    tracker.update(baseline, 100)
    tracker.update(noisy, 200)
    assert tracker.update(None, 300) == []
    assert tracker.update(key(255), 400) == []
    assert tracker.update(key(255), 500) == []
