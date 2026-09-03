"""Video input adapters, including OBS Virtual Camera via OpenCV."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Protocol

from .models import FramePacket


class FrameSource(Protocol):
    source_id: str

    def __iter__(self) -> Iterator[FramePacket]: ...
    def close(self) -> None: ...


class OpenCVCaptureSource:
    """Read a file or camera-like device and yield FramePacket objects."""

    def __init__(self, source: str | int, source_id: str = "opencv", backend: int | None = None) -> None:
        self.source_id, self._source, self._backend, self._capture = source_id, source, backend, None

    def __iter__(self) -> Iterator[FramePacket]:
        try:
            import cv2
        except ImportError as exc:
            raise RuntimeError("需要安装 opencv-python 才能读取视频或摄像头") from exc
        self._capture = (
            cv2.VideoCapture(self._source) if self._backend is None else cv2.VideoCapture(self._source, self._backend)
        )
        if not self._capture.isOpened():
            raise RuntimeError(f"无法打开视频源: {self._source}（设备编号可能不正确，或设备未启动）")
        fps = float(self._capture.get(cv2.CAP_PROP_FPS) or 0.0)
        index = 0
        try:
            while True:
                ok, image = self._capture.read()
                if not ok:
                    break
                timestamp = int(self._capture.get(cv2.CAP_PROP_POS_MSEC) or (index * 1000 / fps if fps else 0))
                yield FramePacket(index, timestamp, image, self.source_id, {"fps": fps})
                index += 1
        finally:
            self.close()

    def close(self) -> None:
        if self._capture is not None:
            self._capture.release()
            self._capture = None


class VideoFileSource(OpenCVCaptureSource):
    def __init__(self, path: str | Path, source_id: str = "obs-video") -> None:
        super().__init__(str(path), source_id)
        self.path = Path(path)


class ObsVirtualCameraSource(OpenCVCaptureSource):
    """Read the scene published by OBS's Start Virtual Camera button."""

    def __init__(self, device_index: int = 0, source_id: str = "obs-virtual-camera") -> None:
        super().__init__(device_index, source_id)
        self.device_index = device_index


class ScreenCaptureSource(OpenCVCaptureSource):
    """Camera-device placeholder for future Desktop Duplication integration."""

    def __init__(self, device_index: int = 0, source_id: str = "screen") -> None:
        super().__init__(device_index, source_id)
        self.device_index = device_index


class CaptureCardSource(OpenCVCaptureSource):
    def __init__(self, device_index: int = 0, source_id: str = "capture-card") -> None:
        super().__init__(device_index, source_id)
        self.device_index = device_index


def make_source(source: str, path: str, device_index: int, source_id: str | None = None) -> FrameSource:
    resolved_id = source_id or source
    if source == "video":
        return VideoFileSource(path, resolved_id)
    if source == "obs-virtual-camera":
        return ObsVirtualCameraSource(device_index, resolved_id)
    if source in {"screen", "camera", "capture-card"}:
        cls = CaptureCardSource if source == "capture-card" else ScreenCaptureSource
        return cls(device_index, resolved_id)
    raise ValueError(f"不支持的视频源类型: {source}")


def list_video_devices(max_index: int = 10) -> list[int]:
    """Return camera-like indexes that OpenCV can open."""
    if max_index < 0:
        raise ValueError("max_index 不能小于 0")
    try:
        import cv2
    except ImportError as exc:
        raise RuntimeError("需要安装 opencv-python 才能枚举视频设备") from exc
    available: list[int] = []
    for index in range(max_index + 1):
        capture = cv2.VideoCapture(index)
        try:
            if capture.isOpened():
                available.append(index)
        finally:
            capture.release()
    return available
