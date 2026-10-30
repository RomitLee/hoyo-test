import json
from threading import Event as ThreadEvent
from time import monotonic

import numpy as np

from hoyo_analyzer.config import AppConfig
from hoyo_analyzer.inventory_app import InventoryMonitorWorker
from hoyo_analyzer.money import MoneyDetector
from hoyo_analyzer.panels import PanelObservation
from hoyo_analyzer.stall_market import OwnStallMarketDetector, StallVisitTracker


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


def test_market_visit_hash_tolerates_small_capture_noise_and_detects_reopen():
    tracker = StallVisitTracker()
    baseline = bytes(32)
    noisy = bytes([0b00000001, *([0] * 31)])
    tracker.update(baseline, 100)
    tracker.update(noisy, 200)
    assert tracker.update(None, 300) == []
    assert tracker.update(key(255), 400) == []
    assert tracker.update(key(255), 500) == []
    events = tracker.update(key(255), 600)
    assert [event.type for event in events] == ["market_stall_changed"]
    assert events[0].payload["message"] == "打开他人收货摊位"


def test_close_then_reopen_same_merchant_is_still_a_new_visit():
    tracker = StallVisitTracker()
    tracker.update(key(7), 100)
    tracker.update(key(7), 200)
    tracker.update(None, 300)
    assert tracker.update(key(7), 400) == []
    assert tracker.update(key(7), 500) == []
    events = tracker.update(key(7), 600)
    assert [event.type for event in events] == ["market_stall_changed"]


def test_slow_market_ocr_uses_persistent_fifo_without_overwriting(tmp_path):
    worker = InventoryMonitorWorker(123, AppConfig(), "梦幻西游 ONLINE - (江苏1区[秦淮风光] - 角色[123456])")
    worker._market_directory = tmp_path
    worker._market_manifest = tmp_path / "queue.json"
    worker._market_queue = []
    started, release = ThreadEvent(), ThreadEvent()

    class SlowDetector:
        @staticmethod
        def analyze(image, panel):
            started.set()
            release.wait(2)

    worker._market_detector = SlowDetector()
    image = np.zeros((480, 640, 3), dtype=np.uint8)
    panel = PanelObservation("stall_other_buy", 1.0, (19, 81, 148, 104))
    before = monotonic()
    worker._enqueue_market_visit(image, panel, 100, 1)
    elapsed = monotonic() - before
    try:
        assert elapsed < 0.1
        assert started.wait(1)
        before = monotonic()
        worker._enqueue_market_visit(image, panel, 200, 2)
        assert monotonic() - before < 0.1
        assert len(worker._market_queue) == 2
        saved = json.loads(worker._market_manifest.read_text(encoding="utf-8"))
        assert [item["timestamp_ms"] for item in saved] == [100, 200]
        assert all((tmp_path / item["file"]).is_file() for item in saved)
    finally:
        release.set()
        worker.stop()


def test_own_stall_name_repairs_only_a_dropped_first_level_prefix():
    recognizer = lambda _mask: ("级太阳石", .95)
    detector = OwnStallMarketDetector(recognizer)
    field = np.full((22, 110, 3), 190, dtype=np.uint8)
    assert detector._vote(field, "name") == ("1级太阳石", .95)

    recognizer = lambda _mask: ("2级舍利子", .95)
    detector = OwnStallMarketDetector(recognizer)
    assert detector._vote(field, "name") is None

    recognizer = lambda _mask: ("太阳石", .95)
    detector = OwnStallMarketDetector(recognizer)
    assert detector._vote(field, "name") is None


def own_stall_price_field(text: str) -> np.ndarray:
    field = np.full((20, 110, 3), (176, 184, 200), dtype=np.uint8)
    digits = MoneyDetector().digits
    x = 3
    for value in text:
        glyph = digits[value]
        height, width = glyph.shape
        area = field[5:5 + height, x:x + width]
        area[glyph > 0] = (20, 20, 20)
        x += width + 1
    return field


def test_own_stall_price_uses_stable_multi_threshold_template_votes():
    detector = OwnStallMarketDetector(lambda _mask: None)
    price = detector._vote(own_stall_price_field("155551"), "price")
    assert price is not None
    assert price[0] == "155551"


def test_own_stall_price_ignores_full_height_scrollbar_contours():
    detector = OwnStallMarketDetector(lambda _mask: None)
    field = np.full((20, 110, 3), (176, 184, 200), dtype=np.uint8)
    field[:, 8:18] = (20, 20, 20)
    field[:, 25:33] = (20, 20, 20)
    assert detector._vote(field, "price") is None
