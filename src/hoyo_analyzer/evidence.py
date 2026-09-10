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

    def save_equipment_tooltip(self, event: Event, packet: FramePacket, output_directory: str | Path) -> str | None:
        """Save a detected equipment tooltip crop and link it from the event payload."""
        if event.type != "equipment_tooltip_opened":
            return None
        bbox = event.payload.get("bbox")
        if not isinstance(bbox, (list, tuple)) or len(bbox) != 4:
            return None
        try:
            x1, y1, x2, y2 = (int(value) for value in bbox)
        except (TypeError, ValueError):
            return None
        height, width = packet.image.shape[:2]
        x1, y1 = max(0, min(width, x1)), max(0, min(height, y1))
        x2, y2 = max(x1, min(width, x2)), max(y1, min(height, y2))
        crop = packet.image[y1:y2, x1:x2]
        if crop.size == 0:
            return None
        try:
            import cv2
            import numpy as np

            directory = Path(output_directory)
            directory.mkdir(parents=True, exist_ok=True)
            path = directory / f"{packet.timestamp_ms:012d}_{event.event_id}_tooltip.png"
            success, encoded = cv2.imencode(".png", crop)
            if not success:
                return None
            np.asarray(encoded).tofile(path)
        except (ImportError, OSError, ValueError):
            return None
        event.payload["tooltip_path"] = str(path)
        return str(path)
