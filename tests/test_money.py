from __future__ import annotations

import base64
import json
from pathlib import Path

import cv2
import numpy as np

from hoyo_analyzer.money import MoneyDetector, MoneyTracker
from hoyo_analyzer.panels import PanelDetector


def test_six_actual_game_amounts_from_panel_location_through_confirmation() -> None:
    samples = json.loads((Path(__file__).parent / "fixtures/money_live_fields.json").read_text())
    for sample in samples:
        image = np.zeros((480, 640, 3), dtype=np.uint8)
        x, y, right, bottom = sample["bbox"]
        decode = lambda raw: cv2.imdecode(np.frombuffer(base64.b64decode(raw), np.uint8), 1)
        image[y:bottom, x:right] = decode(sample["title"])
        for top, raw in zip((y + 273, y + 300), sample["fields"], strict=True):
            image[top:top + 14, x + 74:x + 222] = decode(raw)
        panel = PanelDetector().analyze(image)
        assert panel.panel_type == "inventory"
        detector = MoneyDetector()
        observation = detector.read_fields(image, panel.bbox)
        assert observation == (sample["cash"], None)
        tracker = MoneyTracker()
        for timestamp in (0, 200, 400):
            result = tracker.update(observation, timestamp)
        assert result["cash"] == sample["cash"]
        assert "bank_money" not in result
        assert result["money_readable"] is True


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
    assert "bank_money" not in confirmed
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
    assert detector.analyze(image, (x, y, x + 67, y + 20)) == (1450000, None)
    canvas = np.zeros((600, 800, 3), dtype=np.uint8)
    canvas[60:540, 80:720] = image
    assert detector.analyze(canvas, (x + 80, y + 60, x + 147, y + 80)) == (1450000, None)
    assert detector.analyze(image, None) is None
    assert detector.analyze(image, (600, 450, 667, 470)) is None


def test_cash_confirmation_does_not_depend_on_bank_reading() -> None:
    tracker = MoneyTracker()
    for timestamp in (0, 200, 400):
        result = tracker.update((12179964, None), timestamp)
    assert result["cash"] == 12179964
    assert "bank_money" not in result
    assert result["money_readable"]


def test_slow_analysis_can_confirm_but_pause_clears_pending_evidence() -> None:
    tracker = MoneyTracker()
    for timestamp in (0, 2000, 4000):
        result = tracker.update((1450000, 0), timestamp)
    assert result["cash"] == 1450000
    tracker.update((4995810, 0), 4200)
    tracker.reset_pending()
    assert tracker.update((4995810, 0), 4400)["cash"] == 1450000


def test_grid_location_is_usable_without_panel_title() -> None:
    detector = MoneyDetector()
    image = np.zeros((480, 640, 3), dtype=np.uint8)
    image[348:362, 361:509] = field("1450000")
    image[375:389, 361:509] = field("0")
    assert detector.read_fields(image, None, (305, 131, 560, 335)) == (1450000, None)


def test_reading_fields_independently_survives_bank_cursor() -> None:
    samples = json.loads((Path(__file__).parent / "fixtures/money_fields.json").read_text())
    sample = next(sample for sample in samples if not sample["readable"])
    image = np.zeros((480, 640, 3), dtype=np.uint8)
    for top, raw in zip((348, 375), sample["fields"], strict=True):
        image[top:top + 14, 361:509] = cv2.imdecode(np.frombuffer(base64.b64decode(raw), np.uint8), 1)
    assert MoneyDetector().read_fields(image, (287, 75, 354, 95)) == (sample["cash"], None)
