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
    minimize_window,
)


def test_window_info_display_name():
    window = WindowInfo(0x1234, "梦幻西游", 100, "GameWindow", minimized=True)
    assert window.display_name == "梦幻西游 [已最小化] — 0x1234"


@pytest.mark.skipif(sys.platform != "win32", reason="Windows pointer-sized callback")
def test_window_enumeration_preserves_64_bit_handles(monkeypatch):
    from hoyo_analyzer import wgc

    handle = 0x123456789
    calls = []

    def checked(window_handle, *args):
        assert window_handle.value == handle
        calls.append(window_handle.value)
        return 1

    def read_text(window_handle, buffer, _length):
        checked(window_handle)
        buffer.value = "梦幻西游 ONLINE"
        return len(buffer.value)

    def read_rect(window_handle, pointer):
        checked(window_handle)
        pointer._obj.right, pointer._obj.bottom = 800, 600
        return 1

    user32 = SimpleNamespace(
        EnumWindows=lambda callback, parameter: callback(handle, parameter),
        IsWindowVisible=checked, GetWindowLongW=lambda hwnd, style: checked(hwnd) - 1,
        GetWindowTextLengthW=lambda hwnd: checked(hwnd) + 30, GetWindowTextW=read_text,
        GetWindowRect=read_rect, GetClassNameW=read_text, GetWindowThreadProcessId=checked,
        IsIconic=lambda hwnd: checked(hwnd) - 1,
    )
    monkeypatch.setattr(wgc, "_require_windows", lambda: user32)
    monkeypatch.setattr(wgc.ctypes.windll, "dwmapi", SimpleNamespace(DwmGetWindowAttribute=checked))
    windows = wgc.list_capturable_windows()
    assert len(windows) == 1 and windows[0].hwnd == handle
    assert len(calls) == 9


def test_rect_union_area_does_not_double_count_overlaps():
    rectangles = [(0, 0, 60, 100), (40, 0, 100, 100)]
    assert _rect_union_area(rectangles) == 10_000


def test_window_occlusion_status_reports_any_covered_area():
    status = WindowOcclusionStatus(0x1234, target_area=10_000, occluded_area=100, ratio=0.01)
    assert status.occluded
    assert status.ratio == 0.01


def test_minimize_window_uses_async_windows_request(monkeypatch):
    calls: list[tuple[int, int]] = []
    user32 = SimpleNamespace(ShowWindowAsync=lambda hwnd, command: calls.append((hwnd.value, command)) or 1)
    monkeypatch.setattr("hoyo_analyzer.wgc.is_window_available", lambda _hwnd: True)
    monkeypatch.setattr("hoyo_analyzer.wgc.is_window_minimized", lambda _hwnd: False)
    monkeypatch.setattr("hoyo_analyzer.wgc._require_windows", lambda: user32)

    assert minimize_window(0x1234)
    assert calls == [(0x1234, 6)]


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
    monkeypatch.setattr("hoyo_analyzer.wgc.get_cursor_position_in_frame", lambda _hwnd, _width, _height: (4, 5))

    source = WindowsGraphicsCaptureSource(0x1234)
    iterator = iter(source)
    try:
        packet = next(iterator)
        assert packet.image.shape == (12, 16, 3)
        assert packet.metadata["window_title"] == "梦幻西游"
        assert packet.metadata["cursor_frame_position"] == (4, 5)
    finally:
        iterator.close()
