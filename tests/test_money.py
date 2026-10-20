from __future__ import annotations

import base64
import json
from pathlib import Path

import cv2
import numpy as np

from hoyo_analyzer.money import MoneyDetector, MoneyTracker


def test_provided_cash_fields_and_cursor_occlusion() -> None:
    samples = json.loads((Path(__file__).parent / "fixtures/money_fields.json").read_text())
    detector = MoneyDetector()
    for sample in samples:
        values = [detector._read(cv2.imdecode(np.frombuffer(base64.b64decode(raw), np.uint8), 1))
                  for raw in sample["fields"]]
        assert values[0] == sample["cash"]
        assert values[1] == (0 if sample["readable"] else None)


def field(text: str) -> np.ndarray:
    detector = MoneyDetector()
    image = np.full((14, 148, 3), (200, 184, 176), dtype=np.uint8)
    x = 2
    for digit in text:
        glyph = detector.digits[digit]
        height, width = glyph.shape
        image[3:3 + height, x:x + width][glyph > 0] = 20
        x += width + 1
    return image


def test_money_reads_digits_zero_and_rejects_overlay() -> None:
    detector = MoneyDetector()
    for value in ("1450000", "4995810", "0", "2367", "999999999999"):
        assert detector._read(field(value)) == int(value)
    covered = field("1450000")
    covered[:, 15:20] = (0, 255, 255)
    assert detector._read(covered) is None
    assert detector._read(field("01")) is None
    assert detector._read(np.zeros((14, 148, 3), dtype=np.uint8)) is None


def test_money_tracker_requires_consecutive_frames_and_retains_last_confirmation() -> None:
    tracker = MoneyTracker()
    assert tracker.update((1450000, 0), 0)["money_readable"] is False
    assert tracker.update((1450000, 0), 100)["money_readable"] is False
    confirmed = tracker.update((1450000, 0), 200)
    assert confirmed["cash"] == 1450000
    assert confirmed["bank_money"] == 0
    assert confirmed["money_readable"] is True
    unknown = tracker.update(None, 300)
    assert unknown["cash"] == 1450000
    assert unknown["money_confirmed_at"] == confirmed["money_confirmed_at"]
    assert unknown["money_readable"] is False
    assert tracker.update((4995810, 0), 400)["cash"] == 1450000
    assert tracker.update((4995810, 0), 2500)["cash"] == 1450000
    tracker.update((4995810, 0), 2600)
    assert tracker.update((4995810, 0), 2700)["cash"] == 4995810


def test_money_regions_follow_title_position_and_scale() -> None:
    detector = MoneyDetector()
    image = np.zeros((480, 640, 3), dtype=np.uint8)
    x, y = 287, 75
    image[y + 273:y + 287, x + 74:x + 222] = field("1450000")
    image[y + 300:y + 314, x + 74:x + 222] = field("0")
    assert detector.analyze(image, (x, y, x + 67, y + 20)) == (1450000, 0)
    canvas = np.zeros((600, 800, 3), dtype=np.uint8)
    canvas[60:540, 80:720] = image
    assert detector.analyze(canvas, (x + 80, y + 60, x + 147, y + 80)) == (1450000, 0)
    assert detector.analyze(image, None) is None
    assert detector.analyze(image, (600, 450, 667, 470)) is None
