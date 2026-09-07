import sys
from types import SimpleNamespace

import numpy as np
import pytest

from hoyo_analyzer.capture import make_source
from hoyo_analyzer.wgc import (
    WindowInfo,
    WindowOcclusionStatus,
    WindowsGraphicsCaptureSource,
    _rect_union_area,
)


def test_window_info_display_name():
    window = WindowInfo(0x1234, "梦幻西游", 100, "GameWindow", minimized=True)
    assert window.display_name == "梦幻西游 [已最小化] — 0x1234"


def test_rect_union_area_does_not_double_count_overlaps():
    rectangles = [(0, 0, 60, 100), (40, 0, 100, 100)]
    assert _rect_union_area(rectangles) == 10_000


def test_window_occlusion_status_reports_any_covered_area():
    status = WindowOcclusionStatus(0x1234, target_area=10_000, occluded_area=100, ratio=0.01)
    assert status.occluded
    assert status.ratio == 0.01


def test_wgc_factory():
    source = make_source(
        "windows-graphics-capture",
        "unused",
        0,
        window_hwnd=0x1234,
    )
    assert isinstance(source, WindowsGraphicsCaptureSource)
    assert source.window_hwnd == 0x1234
    assert source.source_id == "windows-graphics-capture"


def test_wgc_factory_requires_hwnd():
    with pytest.raises(ValueError, match="需要选择目标窗口"):
        make_source("windows-graphics-capture", "unused", 0)


def test_wgc_allows_minimized_window_and_consumes_background_frame(monkeypatch):
    class FakeControl:
        def __init__(self):
            self.stopped = False

        def stop(self):
            self.stopped = True

        def is_finished(self):
            return False

    class FakeCapture:
        def __init__(self, **_kwargs):
            self.frame_handler = None
            self.closed_handler = None
            self.control = FakeControl()

        def start_free_threaded(self):
            frame = SimpleNamespace(frame_buffer=np.zeros((12, 16, 4), dtype=np.uint8))
            self.frame_handler(frame, self.control)
            return self.control

    monkeypatch.setitem(sys.modules, "windows_capture", SimpleNamespace(WindowsCapture=FakeCapture))
    monkeypatch.setattr("hoyo_analyzer.wgc.is_window_available", lambda _hwnd: True)
    monkeypatch.setattr("hoyo_analyzer.wgc.is_window_minimized", lambda _hwnd: True)
    monkeypatch.setattr("hoyo_analyzer.wgc.get_window_title", lambda _hwnd: "梦幻西游")

    source = WindowsGraphicsCaptureSource(0x1234)
    iterator = iter(source)
    try:
        packet = next(iterator)
        assert packet.image.shape == (12, 16, 3)
        assert packet.metadata["window_title"] == "梦幻西游"
    finally:
        iterator.close()
