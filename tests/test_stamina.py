from datetime import datetime, timedelta, timezone
from pathlib import Path

import cv2
import pytest

from hoyo_analyzer.panels import PanelDetector
from hoyo_analyzer.stamina import StaminaDetector, StaminaTracker, stamina_full_at

FIXTURES = Path(__file__).parent / "fixtures" / "stamina"


@pytest.mark.parametrize("filename,expected", [
    ("level141-314of863.png", (141, 314, 863)),
    ("level144-668of914.png", (144, 668, 914)),
])
def test_real_attribute_glyphs_read_at_both_game_sizes(filename, expected):
    original = cv2.imread(str(FIXTURES / filename))
    detector = StaminaDetector()
    panel_detector = PanelDetector()
    for image in (original, cv2.resize(original, (800, 600), interpolation=cv2.INTER_LINEAR)):
        panel = panel_detector.analyze(image)
        reading = detector.analyze(image, panel)
        assert reading is not None
        assert (reading.level, reading.current, reading.maximum) == expected


def test_stamina_estimate_uses_whole_five_minute_ticks_and_requires_confirmation():
    image = cv2.imread(str(FIXTURES / "level141-314of863.png"))
    panel = PanelDetector().analyze(image)
    reading = StaminaDetector().analyze(image, panel)
    assert reading is not None
    observed = datetime(2026, 10, 27, 10, 57, 42, tzinfo=timezone(timedelta(hours=8)))
    assert stamina_full_at(reading, observed).isoformat() == "2026-10-27T14:47:42+08:00"
    tracker = StaminaTracker()
    assert tracker.update(reading, 100) == {}
    confirmed = tracker.update(reading, 200)
    assert confirmed["stamina_current"] == 314
    assert tracker.update(None, 300)["stamina_current"] == 314
    covered = image.copy()
    covered[183:199, 493:529] = 255
    assert StaminaDetector().analyze(covered, panel) is None
