"""Timestamp-based FPS sampling and bounded live buffering."""

from __future__ import annotations

from collections import deque
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from threading import Lock

from .models import FramePacket


@dataclass(slots=True)
class SamplingProfile:
    normal_fps: float = 5.0
    battle_fps: float = 10.0
    burst_fps: float = 20.0
    burst_duration_ms: int = 1500


class AdaptiveSampler:
    def __init__(self, profile: SamplingProfile | None = None) -> None:
        self.profile = profile or SamplingProfile()
        self.mode = "normal"
        self._next_due_ms = None
        self._burst_until_ms = -1

    def set_mode(self, mode: str, now_ms: int | None = None) -> None:
        if mode not in {"normal", "battle", "burst"}:
            raise ValueError(f"未知抽帧模式: {mode}")
        self.mode = mode
        self._next_due_ms = None
        if mode == "burst" and now_ms is not None:
            self._burst_until_ms = now_ms + self.profile.burst_duration_ms

    def _fps(self, timestamp_ms: int) -> float:
        if self.mode == "burst" and timestamp_ms >= self._burst_until_ms:
            self.mode = "normal"
        return {"normal": self.profile.normal_fps, "battle": self.profile.battle_fps, "burst": self.profile.burst_fps}[
            self.mode
        ]

    def accept(self, packet: FramePacket) -> bool:
        fps = self._fps(packet.timestamp_ms)
        interval = max(1.0, 1000.0 / fps)
        if self._next_due_ms is None:
            self._next_due_ms = packet.timestamp_ms
        if packet.timestamp_ms + 0.01 < self._next_due_ms:
            return False
        while self._next_due_ms <= packet.timestamp_ms:
            self._next_due_ms += interval
        return True

    def sample(self, packets: Iterable[FramePacket]) -> Iterator[FramePacket]:
        for packet in packets:
            if self.accept(packet):
                yield packet


class LatestFrameQueue:
    """Bounded live queue. When full, discard the oldest frame to avoid latency growth."""

    def __init__(self, maxsize: int = 120) -> None:
        if maxsize < 1:
            raise ValueError("maxsize 必须大于 0")
        self._items = deque(maxlen=maxsize)
        self._lock = Lock()
        self.dropped = 0

    def put(self, packet: FramePacket) -> None:
        with self._lock:
            if len(self._items) == self._items.maxlen:
                self._items.popleft()
                self.dropped += 1
            self._items.append(packet)

    def get(self) -> FramePacket | None:
        """Return the newest frame and discard any older frames waiting behind it.

        This queue is used by the live pipeline, where freshness is more
        important than processing every captured frame. Returning from the
        left side would turn the queue into a FIFO backlog: if capture runs
        at 60 FPS and analysis runs at 5 FPS, a queue of 120 frames can add
        roughly two seconds of latency.
        """
        with self._lock:
            if not self._items:
                return None

            latest = self._items.pop()
            discarded = len(self._items)
            if discarded:
                self.dropped += discarded
                self._items.clear()
            return latest

    def __len__(self) -> int:
        with self._lock:
            return len(self._items)
