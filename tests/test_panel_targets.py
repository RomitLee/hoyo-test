from pathlib import Path

import numpy as np
import pytest

from hoyo_analyzer.panel_targets import (
    LocalTextRecognizer,
    PanelTargetDetector,
    PanelTargetTracker,
    TargetReading,
    attach_target,
)
from hoyo_analyzer.panels import PanelObservation, PanelTracker

EXPECTED = [("剑侠旧梦", 109), ("图图防御符4", 144), ("昌少带你飞", 109), ("惜别离", 175)]


def sample(index):
    # Only the two text fields from user-provided captures, no game or account
    # screenshots. These are regression fixtures, never a recognition dictionary.
    fields = np.load(Path(__file__).parent / "fixtures" / "panel_target_fields.npz")
    image = np.zeros((480, 640, 3), dtype=np.uint8)
    kind = "give" if index < 2 else "trade"
    panel = PanelObservation(kind, 1, (300, 21, 340, 42) if index < 2 else (330, 14, 369, 34))
    x, y, *_ = panel.bbox
    rects = ((-72, 372, 24, 392), (60, 372, 105, 392)) if index < 2 else (
        (-145, 165, -44, 186), (-172, 194, -135, 214))
    for j, (left, top, right, bottom) in enumerate(rects):
        image[y + top:y + bottom, x + left:x + right] = fields[f"field_{index}_{j}"]
    return image, panel


@pytest.mark.parametrize("index", range(4))
def test_real_text_fields_recognize_nickname_and_level(index):
    image, panel = sample(index)
    reading = PanelTargetDetector().analyze(image, panel)
    assert reading.identity == EXPECTED[index], reading.reason


def test_title_anchor_handles_moved_dialog_and_native_ui_in_800x600():
    image, panel = sample(1)
    moved = np.zeros((600, 800, 3), dtype=np.uint8)
    moved[50:530, 80:720] = image
    translated = PanelObservation("give", 1, tuple(v + (80 if i % 2 == 0 else 50) for i, v in enumerate(panel.bbox)))
    assert PanelTargetDetector().analyze(moved, translated).identity == EXPECTED[1]


def test_cursor_or_partial_crop_never_returns_truncated_player_name():
    image, panel = sample(0)
    image[397:405, 240:248] = (255, 255, 0)
    assert PanelTargetDetector().analyze(image, panel).identity is None
    image, panel = sample(0)
    assert PanelTargetDetector().analyze(image[:400], panel).identity is None


def test_unrelated_or_blank_panel_does_not_initialize_ocr():
    class Forbidden:
        def recognize(self, image):
            raise AssertionError("OCR should not run")

    detector = PanelTargetDetector(Forbidden())
    blank = np.zeros((480, 640, 3), np.uint8)
    assert detector.analyze(blank, PanelObservation("inventory")).identity is None
    assert detector.analyze(blank, PanelObservation("give", 1, (300, 21, 340, 42))).identity is None


def test_failure_is_nonfatal_and_readings_are_cached():
    class Broken:
        count = 0

        def recognize(self, image):
            self.count += 1
            raise RuntimeError("model unavailable")

    engine = Broken()
    detector = PanelTargetDetector(engine)
    image, panel = sample(0)
    assert detector.analyze(image, panel).identity is None
    assert detector.analyze(image, panel).identity is None
    assert engine.count == 1


def test_name_and_level_are_confirmed_as_one_pair_and_switch_does_not_reuse_previous_name():
    tracker = PanelTargetTracker()
    a, b = TargetReading("甲", 109, 0.99), TargetReading("乙", 144, 0.99)
    assert tracker.update("give", a, 0) == []
    assert tracker.update("give", a, 200) == []
    events = tracker.update("give", a, 400)
    assert events[0].payload["target_name"] == "甲"
    assert tracker.update("give", a, 600) == []
    assert tracker.update("give", b, 800) == []
    assert tracker.current is None
    assert tracker.update("give", b, 1000) == []
    event = tracker.update("give", b, 1200)[0]
    assert event.type == "panel_target_changed"
    assert event.payload["target_name"] == "乙"
    assert event.payload["target_level"] == 144
    assert "乙，144级" in event.display_name


def test_failure_clears_display_immediately_then_logs_unrecognized_once():
    tracker = PanelTargetTracker()
    for t in (0, 200, 400):
        tracker.update("trade", TargetReading("甲", 175, 0.99), t)
    assert tracker.update("trade", TargetReading(), 600) == []
    assert tracker.current is None
    tracker.update("trade", TargetReading(), 800)
    event = tracker.update("trade", TargetReading(), 1000)[0]
    assert event.payload["target_name"] is None
    assert event.payload["target_level"] is None
    assert event.payload["target_status"] == "unrecognized"
    assert tracker.update("trade", TargetReading(), 1200) == []


def test_pause_gap_unknown_and_repeated_timestamp_cannot_confirm_old_candidate():
    tracker = PanelTargetTracker()
    reading = TargetReading("甲", 109, 0.99)
    for t in (0, 200):
        tracker.update("give", reading, t)
    assert tracker.update("give", reading, 200) == []
    assert tracker.current is None
    tracker.reset()
    assert tracker.update("give", reading, 20000) == []
    assert tracker.update("give", reading, 22000) == []
    tracker.update("unknown", reading, 22200)
    assert tracker.update("give", reading, 22400) == []
    assert tracker.current is None


def test_open_panel_log_contains_confirmed_target_without_claiming_completion():
    panels, targets = PanelTracker(), PanelTargetTracker()
    for timestamp in (0, 200, 400):
        events = panels.update(PanelObservation("trade", 1), timestamp)
        targets.update("trade", TargetReading("惜别离", 175, 0.99), timestamp)
    assert len(events) == 1
    attach_target(events[0], targets.current)
    assert events[0].display_name == "打开交易面板，对象：惜别离，175级"
    assert "完成" not in events[0].display_name


def test_model_inference_uses_cpu_only_and_does_not_require_rapidocr():
    recognizer = LocalTextRecognizer()
    assert recognizer.session.get_providers() == ["CPUExecutionProvider"]
    # A completely white crop is never hallucinated into a player identity.
    assert recognizer.recognize(np.zeros((20, 100), np.uint8)) is None


def test_slave_forwarder_keeps_recipient_in_monitor_logs_without_sending_alert():
    from types import SimpleNamespace

    from hoyo_analyzer.multi_inventory_app import InventoryMonitorWindow

    tracker = PanelTargetTracker()
    for t in (0, 200, 400):
        events = tracker.update("give", TargetReading("甲", 109, 0.99), t)
    payloads, lines = [], []
    session = SimpleNamespace(minimized=False, account=SimpleNamespace(secondary_region="区服", role_name="角色", role_id="1234"))
    fake_window = SimpleNamespace(sessions={123: session}, monitoring_paused=False,
                                  log=SimpleNamespace(appendPlainText=lines.append),
                                  relay_client=SimpleNamespace(send_monitor_payload=payloads.append))
    InventoryMonitorWindow._on_event(fake_window, 123, events[0])
    assert payloads[0]["event_type"] == "panel_target_changed"
    assert payloads[0]["target_name"] == "甲" and payloads[0]["target_level"] == 109
    assert payloads[0]["character_id"] == "1234"
    assert payloads[0]["window_id"] == "hwnd:123"
    assert "甲，109级" in lines[0]
