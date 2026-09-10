"""Windows Graphics Capture helpers for capturing a specific application window."""

from __future__ import annotations

import ctypes
import os
from contextlib import suppress
from ctypes import byref, c_int, create_unicode_buffer, sizeof, wintypes
from dataclasses import dataclass
from itertools import pairwise
from queue import Empty, Full, Queue
from threading import Event
from time import monotonic
from typing import Any

import numpy as np

from .models import FramePacket


@dataclass(frozen=True, slots=True)
class WindowInfo:
    """A visible top-level Windows window that can be offered in the UI."""

    hwnd: int
    title: str
    process_id: int
    class_name: str
    minimized: bool = False

    @property
    def display_name(self) -> str:
        state = " [已最小化]" if self.minimized else ""
        return f"{self.title}{state} — 0x{self.hwnd:X}"


@dataclass(frozen=True, slots=True)
class OccludingWindow:
    """One higher-Z-order window intersecting the capture target."""

    hwnd: int
    title: str
    process_id: int
    class_name: str
    overlap_area: int

    @property
    def display_name(self) -> str:
        return self.title or self.class_name or f"窗口 0x{self.hwnd:X}"


@dataclass(frozen=True, slots=True)
class WindowOcclusionStatus:
    """Geometric occlusion estimate for a top-level window."""

    hwnd: int
    target_area: int
    occluded_area: int
    ratio: float
    occluders: tuple[OccludingWindow, ...] = ()
    minimized: bool = False

    @property
    def occluded(self) -> bool:
        return self.occluded_area > 0


def _require_windows() -> Any:
    if os.name != "nt":
        raise RuntimeError("Windows Graphics Capture 只能在 Windows 10/11 上使用")
    return ctypes.windll.user32


def is_window_available(hwnd: int) -> bool:
    """Return whether an HWND still identifies a real top-level window."""
    if os.name != "nt" or not hwnd:
        return False
    user32 = ctypes.windll.user32
    return bool(user32.IsWindow(wintypes.HWND(hwnd)))


def is_window_minimized(hwnd: int) -> bool:
    """Return whether the selected window is currently minimized."""
    if not is_window_available(hwnd):
        return False
    return bool(ctypes.windll.user32.IsIconic(wintypes.HWND(hwnd)))


def get_window_title(hwnd: int) -> str:
    """Read the current title of an HWND."""
    if not is_window_available(hwnd):
        return ""
    user32 = ctypes.windll.user32
    length = user32.GetWindowTextLengthW(wintypes.HWND(hwnd))
    if length <= 0:
        return ""
    buffer = create_unicode_buffer(length + 1)
    user32.GetWindowTextW(wintypes.HWND(hwnd), buffer, length + 1)
    return buffer.value.strip()


def get_cursor_position_in_frame(hwnd: int, frame_width: int, frame_height: int) -> tuple[int, int] | None:
    """Map the system cursor to the captured client-frame coordinate system."""
    if not is_window_available(hwnd) or frame_width <= 0 or frame_height <= 0:
        return None
    user32 = ctypes.windll.user32
    point = wintypes.POINT()
    client_rect = wintypes.RECT()
    if not user32.GetCursorPos(byref(point)):
        return None
    if not user32.ScreenToClient(wintypes.HWND(hwnd), byref(point)):
        return None
    if not user32.GetClientRect(wintypes.HWND(hwnd), byref(client_rect)):
        return None
    client_width = int(client_rect.right - client_rect.left)
    client_height = int(client_rect.bottom - client_rect.top)
    if client_width <= 0 or client_height <= 0:
        return None
    if point.x < 0 or point.y < 0 or point.x >= client_width or point.y >= client_height:
        return None
    return (
        min(frame_width - 1, max(0, round(point.x * frame_width / client_width))),
        min(frame_height - 1, max(0, round(point.y * frame_height / client_height))),
    )


def _window_bounds(hwnd: int) -> tuple[int, int, int, int] | None:
    """Return visible frame bounds, excluding the invisible DWM resize border."""
    if not is_window_available(hwnd):
        return None
    rect = wintypes.RECT()
    dwmwa_extended_frame_bounds = 9
    result = ctypes.windll.dwmapi.DwmGetWindowAttribute(
        wintypes.HWND(hwnd),
        dwmwa_extended_frame_bounds,
        byref(rect),
        sizeof(rect),
    )
    if result != 0 and not ctypes.windll.user32.GetWindowRect(wintypes.HWND(hwnd), byref(rect)):
        return None
    bounds = (int(rect.left), int(rect.top), int(rect.right), int(rect.bottom))
    if bounds[2] <= bounds[0] or bounds[3] <= bounds[1]:
        return None
    return bounds


def _intersection(
    first: tuple[int, int, int, int],
    second: tuple[int, int, int, int],
) -> tuple[int, int, int, int] | None:
    left = max(first[0], second[0])
    top = max(first[1], second[1])
    right = min(first[2], second[2])
    bottom = min(first[3], second[3])
    return (left, top, right, bottom) if right > left and bottom > top else None


def _rect_union_area(rectangles: list[tuple[int, int, int, int]]) -> int:
    """Calculate the union area of axis-aligned rectangles without double counting."""
    if not rectangles:
        return 0
    x_edges = sorted({edge for rect in rectangles for edge in (rect[0], rect[2])})
    area = 0
    for left, right in pairwise(x_edges):
        if right <= left:
            continue
        y_intervals = sorted((rect[1], rect[3]) for rect in rectangles if rect[0] < right and rect[2] > left)
        if not y_intervals:
            continue
        covered_y = 0
        start, end = y_intervals[0]
        for next_start, next_end in y_intervals[1:]:
            if next_start <= end:
                end = max(end, next_end)
            else:
                covered_y += end - start
                start, end = next_start, next_end
        covered_y += end - start
        area += (right - left) * covered_y
    return area


def measure_window_occlusion(hwnd: int) -> WindowOcclusionStatus:
    """Estimate how much of a window is covered by higher-Z-order windows.

    This is a fast geometry/Z-order check intended for user warnings. It cannot
    perfectly model transparent, click-through, or irregularly shaped windows.
    """
    if not is_window_available(hwnd):
        raise RuntimeError("目标游戏窗口已经关闭")

    target_bounds = _window_bounds(hwnd)
    if target_bounds is None:
        raise RuntimeError("无法读取目标游戏窗口的位置和大小")
    target_area = (target_bounds[2] - target_bounds[0]) * (target_bounds[3] - target_bounds[1])
    if is_window_minimized(hwnd):
        return WindowOcclusionStatus(hwnd, target_area, 0, 0.0, minimized=True)

    user32 = ctypes.windll.user32
    dwmapi = ctypes.windll.dwmapi
    dwmwa_cloaked = 14
    z_order: list[int] = []
    enum_proc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    @enum_proc
    def callback(candidate: int, _lparam: int) -> bool:
        z_order.append(int(candidate))
        return True

    if not user32.EnumWindows(callback, 0):
        raise OSError("枚举 Windows 窗口层级失败")
    try:
        target_index = z_order.index(int(hwnd))
    except ValueError as exc:
        raise RuntimeError("目标游戏窗口不在当前桌面窗口层级中") from exc

    intersections: list[tuple[int, int, int, int]] = []
    occluders: list[OccludingWindow] = []
    for candidate in z_order[:target_index]:
        candidate_hwnd = wintypes.HWND(candidate)
        if not user32.IsWindowVisible(candidate_hwnd) or user32.IsIconic(candidate_hwnd):
            continue
        cloaked = c_int(0)
        if (
            dwmapi.DwmGetWindowAttribute(candidate_hwnd, dwmwa_cloaked, byref(cloaked), sizeof(cloaked)) == 0
            and cloaked.value
        ):
            continue
        candidate_bounds = _window_bounds(candidate)
        if candidate_bounds is None:
            continue
        overlap = _intersection(target_bounds, candidate_bounds)
        if overlap is None:
            continue
        overlap_area = (overlap[2] - overlap[0]) * (overlap[3] - overlap[1])
        title = get_window_title(candidate)
        class_buffer = create_unicode_buffer(256)
        user32.GetClassNameW(candidate_hwnd, class_buffer, len(class_buffer))
        process_id = wintypes.DWORD()
        user32.GetWindowThreadProcessId(candidate_hwnd, byref(process_id))
        intersections.append(overlap)
        occluders.append(
            OccludingWindow(
                hwnd=candidate,
                title=title,
                process_id=int(process_id.value),
                class_name=class_buffer.value,
                overlap_area=overlap_area,
            )
        )

    occluded_area = min(target_area, _rect_union_area(intersections))
    occluders.sort(key=lambda item: item.overlap_area, reverse=True)
    return WindowOcclusionStatus(
        hwnd=hwnd,
        target_area=target_area,
        occluded_area=occluded_area,
        ratio=occluded_area / target_area if target_area else 0.0,
        occluders=tuple(occluders),
    )


def list_capturable_windows() -> list[WindowInfo]:
    """Enumerate visible, non-cloaked top-level windows with non-empty titles."""
    user32 = _require_windows()
    dwmapi = ctypes.windll.dwmapi
    windows: list[WindowInfo] = []
    enum_proc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    dwmwa_cloaked = 14
    gwl_exstyle = -20
    ws_ex_toolwindow = 0x00000080

    @enum_proc
    def callback(hwnd: int, _lparam: int) -> bool:
        if not user32.IsWindowVisible(hwnd):
            return True
        if user32.GetWindowLongW(hwnd, gwl_exstyle) & ws_ex_toolwindow:
            return True

        cloaked = c_int(0)
        if dwmapi.DwmGetWindowAttribute(hwnd, dwmwa_cloaked, byref(cloaked), sizeof(cloaked)) == 0 and cloaked.value:
            return True

        length = user32.GetWindowTextLengthW(hwnd)
        if length <= 0:
            return True
        title_buffer = create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, title_buffer, length + 1)
        title = title_buffer.value.strip()
        if not title:
            return True

        rect = wintypes.RECT()
        if not user32.GetWindowRect(hwnd, byref(rect)) or rect.right <= rect.left or rect.bottom <= rect.top:
            return True

        class_buffer = create_unicode_buffer(256)
        user32.GetClassNameW(hwnd, class_buffer, len(class_buffer))
        process_id = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, byref(process_id))
        windows.append(
            WindowInfo(
                hwnd=int(hwnd),
                title=title,
                process_id=int(process_id.value),
                class_name=class_buffer.value,
                minimized=bool(user32.IsIconic(hwnd)),
            )
        )
        return True

    if not user32.EnumWindows(callback, 0):
        raise OSError("枚举 Windows 窗口失败")
    return sorted(windows, key=lambda item: item.title.casefold())


_CAPTURE_CLOSED = object()


class WindowsGraphicsCaptureSource:
    """Yield BGR NumPy frames from one HWND through Windows Graphics Capture."""

    def __init__(
        self,
        window_hwnd: int,
        source_id: str = "windows-graphics-capture",
        *,
        cursor_capture: bool = False,
        minimum_update_interval_ms: int | None = None,
        first_frame_timeout_seconds: float = 8.0,
        queue_size: int = 2,
    ) -> None:
        if not window_hwnd:
            raise ValueError("Windows Graphics Capture 需要有效的目标窗口")
        self.window_hwnd = int(window_hwnd)
        self.source_id = source_id
        self.cursor_capture = cursor_capture
        self.minimum_update_interval_ms = (
            max(1, int(minimum_update_interval_ms)) if minimum_update_interval_ms is not None else None
        )
        self.first_frame_timeout_seconds = max(1.0, float(first_frame_timeout_seconds))
        self._frames: Queue[tuple[np.ndarray, int, tuple[int, int] | None] | object] = Queue(maxsize=max(1, queue_size))
        self._stop_requested = Event()
        self._capture_closed = Event()
        self._capture: Any = None
        self._control: Any = None
        self._started = False

    def _offer_latest(self, item: tuple[np.ndarray, int, tuple[int, int] | None] | object) -> None:
        try:
            self._frames.put_nowait(item)
            return
        except Full:
            pass
        try:
            self._frames.get_nowait()
        except Empty:
            pass
        try:
            self._frames.put_nowait(item)
        except Full:
            pass

    def _on_frame_arrived(self, frame: Any, capture_control: Any) -> None:
        if self._stop_requested.is_set():
            capture_control.stop()
            return
        # windows-capture exposes a zero-copy BGRA view whose native owner is only
        # guaranteed during the callback, so copy before crossing thread boundaries.
        image = frame.frame_buffer[:, :, :3].copy()
        height, width = image.shape[:2]
        cursor_position = get_cursor_position_in_frame(self.window_hwnd, width, height)
        self._offer_latest((image, int(monotonic() * 1000), cursor_position))

    def _on_closed(self) -> None:
        self._capture_closed.set()
        self._offer_latest(_CAPTURE_CLOSED)

    def __iter__(self):
        if self._started:
            raise RuntimeError("同一个 Windows Graphics Capture 视频源不能重复启动")
        if not is_window_available(self.window_hwnd):
            raise RuntimeError("目标游戏窗口已经关闭，请刷新窗口列表后重新选择")
        try:
            from windows_capture import WindowsCapture
        except ImportError as exc:
            raise RuntimeError("缺少 Windows Graphics Capture 依赖，请执行：uv sync") from exc

        self._started = True
        started_at = monotonic()
        first_frame_deadline = started_at + self.first_frame_timeout_seconds
        frame_index = 0
        title = get_window_title(self.window_hwnd)

        try:
            try:
                self._capture = WindowsCapture(
                    window_hwnd=self.window_hwnd,
                    cursor_capture=self.cursor_capture,
                    draw_border=None,
                    minimum_update_interval=self.minimum_update_interval_ms,
                )
                self._capture.frame_handler = self._on_frame_arrived
                self._capture.closed_handler = self._on_closed
                self._control = self._capture.start_free_threaded()
            except Exception as exc:
                raise RuntimeError(f"Windows Graphics Capture 启动失败：{exc}") from exc

            while not self._stop_requested.is_set():
                try:
                    item = self._frames.get(timeout=0.2)
                except Empty:
                    if not is_window_available(self.window_hwnd):
                        raise RuntimeError("目标游戏窗口已经关闭，Windows窗口采集已停止")

                    # Most applications stop presenting new frames while minimized.
                    # Keep the WGC session alive instead of terminating analysis;
                    # Windows normally resumes frame delivery after the window is
                    # restored. If the game keeps rendering in the background, its
                    # minimized frames are consumed normally by the callback.
                    if is_window_minimized(self.window_hwnd):
                        first_frame_deadline = monotonic() + self.first_frame_timeout_seconds
                        continue

                    if self._control is not None and self._control.is_finished():
                        raise RuntimeError("Windows Graphics Capture 会话意外结束")
                    if frame_index == 0 and monotonic() >= first_frame_deadline:
                        raise RuntimeError("8秒内没有收到游戏画面，请确认目标窗口可以被系统采集")
                    continue

                if item is _CAPTURE_CLOSED:
                    if not self._stop_requested.is_set():
                        raise RuntimeError("目标游戏窗口或 Windows Graphics Capture 会话已经关闭")
                    break

                image, captured_at_ms, cursor_position = item
                timestamp_ms = max(0, captured_at_ms - int(started_at * 1000))
                yield FramePacket(
                    frame_index,
                    timestamp_ms,
                    image,
                    self.source_id,
                    {
                        "capture": "windows-graphics-capture",
                        "window_hwnd": self.window_hwnd,
                        "window_title": title,
                        "cursor_frame_position": cursor_position,
                    },
                )
                frame_index += 1
        finally:
            self.close()

    def close(self) -> None:
        self._stop_requested.set()
        control, self._control = self._control, None
        if control is not None:
            # The native package exposes untyped shutdown failures. Closing is
            # idempotent and best effort, so a shutdown error must not mask the
            # original capture exception.
            with suppress(Exception):
                control.stop()
        self._offer_latest(_CAPTURE_CLOSED)
        self._capture = None
