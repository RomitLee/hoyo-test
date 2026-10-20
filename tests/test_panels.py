from datetime import UTC, datetime
from types import SimpleNamespace

import numpy as np

from hoyo_analyzer.panels import PanelDetector, PanelObservation, PanelTracker
from hoyo_analyzer.storage import event_to_dict


def confirm(tracker, kind, start=0, count=3):
    events = []
    for index in range(count):
        events.extend(tracker.update(PanelObservation(kind, 0.99), start + index * 200,
                                     occurred_at=datetime(2026, 10, 20, 2, 31, 20, tzinfo=UTC)))
    return events


def test_panel_transition_records_beijing_time_once_and_retains_identity():
    tracker = PanelTracker()
    opened = confirm(tracker, "inventory")
    assert len(opened) == 1
    assert opened[0].display_time == "2026年10月20日10:31:20"
    assert opened[0].display_name == "打开背包"
    assert opened[0].payload["occurred_at"].startswith("2026-10-20T10:31:20")
    assert event_to_dict(opened[0])["event_type"] == "panel_opened"
    assert confirm(tracker, "inventory", 600, 20) == []
    switched = confirm(tracker, "stall", 5000)
    assert [event.display_name for event in switched] == ["打开摊位面板"]
    assert switched[0].event_id != opened[0].event_id


def test_default_needs_sustained_absence_and_unknown_does_not_close_panel():
    tracker = PanelTracker()
    confirm(tracker, "trade")
    assert confirm(tracker, "default", 1000) == []
    tracker.update(PanelObservation("unknown"), 1500)
    assert tracker.stable == "trade"
    assert confirm(tracker, "trade", 1600) == []
    default = confirm(tracker, "default", 3000, 6)
    assert [event.display_name for event in default] == ["关闭面板，返回默认游戏界面"]
    assert default[0].type == "game_default"
    assert confirm(tracker, "default", 5000, 10) == []


def test_pause_resets_pending_confirmation_without_reopening_same_panel():
    tracker = PanelTracker()
    confirm(tracker, "attributes")
    assert confirm(tracker, "give", 1000, 2) == []
    tracker.reset_pending()
    assert confirm(tracker, "give", 20000, 1) == []
    tracker.reset_pending()
    assert confirm(tracker, "attributes", 21000) == []


def test_missing_templates_or_black_capture_are_unknown(tmp_path):
    frame = np.zeros((480, 640, 3), dtype=np.uint8)
    assert PanelDetector(tmp_path).analyze(frame).panel_type == "unknown"
    assert PanelDetector().analyze(frame).panel_type == "unknown"
    assert PanelDetector().analyze(np.array([])).panel_type == "unknown"


def test_stall_record_button_colour_cannot_impersonate_blue_title():
    detector = PanelDetector()
    title = detector.templates["stall_records"][0].copy()
    image = np.zeros((480, 640, 3), dtype=np.uint8)
    # Preserve white letters but put them on a red button instead of a blue title.
    background = title.min(axis=2) < 160
    title[background] = (40, 20, 180)
    image[300:300 + title.shape[0], 200:200 + title.shape[1]] = title
    assert detector.analyze(image).panel_type == "unknown"


def test_live_worker_writes_confirmed_panel_actions_with_character_identity(monkeypatch, tmp_path):
    from hoyo_analyzer.config import AppConfig
    from hoyo_analyzer.inventory_app import InventoryMonitorWorker
    from hoyo_analyzer.inventory_fullness import InventoryAnalysis, InventoryStatus
    from hoyo_analyzer.models import FramePacket

    frames = [FramePacket(index, index * 200, np.zeros((48, 64, 3), np.uint8)) for index in range(9)]

    class Source:
        def __iter__(self):
            return iter(frames)

        def close(self):
            pass

    observed = [PanelObservation("inventory", 0.99)] * 3 + [PanelObservation("default", 0.99)] * 6
    monkeypatch.setattr("hoyo_analyzer.inventory_app.make_source", lambda *args, **kwargs: Source())
    monkeypatch.setattr("hoyo_analyzer.inventory_app.is_window_minimized", lambda hwnd: False)
    monkeypatch.setattr("hoyo_analyzer.inventory_app.PanelDetector", lambda: SimpleNamespace(analyze=lambda image: observed.pop(0)))
    monkeypatch.setattr("hoyo_analyzer.inventory_app.InventoryFullnessDetector", lambda path: SimpleNamespace(
        analyze=lambda image: InventoryAnalysis(InventoryStatus.NOT_FULL, 0.96, "sample")))
    config = AppConfig()
    config.output.event_log = str(tmp_path / "events.jsonl")
    config.sampling.normal_fps = 5
    worker = InventoryMonitorWorker(123, config, "梦幻西游 ONLINE - (江苏1区[秦淮风光] - 清风知夏[54588235])")
    events, errors = [], []
    worker.event_ready.connect(events.append)
    worker.failed.connect(errors.append)
    worker.run()
    assert errors == []
    assert [event.display_name for event in events] == ["打开背包", "关闭面板，返回默认游戏界面"]
    assert all(event.payload["role_name"] == "清风知夏" for event in events)
    lines = (tmp_path / "panel_events.log").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    assert "打开背包" in lines[0] and "秦淮风光/清风知夏" in lines[0]
