"""Evidence-frame persistence for debugging recognition mistakes."""

from __future__ import annotations

import re
from collections import deque
from pathlib import Path

from .models import Event, FramePacket


class EvidenceWriter:
    def __init__(self, directory: str | Path = "runtime/evidence", pre_buffer_ms: int = 2000) -> None:
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.pre_buffer_ms = pre_buffer_ms
        self._buffer = deque()

    def add(self, packet: FramePacket) -> None:
        self._buffer.append(packet)
        cutoff = packet.timestamp_ms - self.pre_buffer_ms
        while self._buffer and self._buffer[0].timestamp_ms < cutoff:
            self._buffer.popleft()

    def save(self, event: Event, packet: FramePacket) -> str | None:
        try:
            import cv2
        except ImportError:
            return None
        name = re.sub(r"[^0-9A-Za-z_-]+", "_", event.type).strip("_") or "event"
        path = self.directory / f"{packet.timestamp_ms:012d}_{event.event_id}_{name}.jpg"
        if not cv2.imwrite(str(path), packet.image):
            return None
        event.evidence_path = str(path)
        return str(path)
