"""Read character stamina on the attributes panel and estimate recovery time."""

from __future__ import annotations

import re
from collections import Counter, OrderedDict
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from time import monotonic

import cv2
import numpy as np

from .panel_targets import recognize_shared_text
from .panels import PanelObservation


@dataclass(frozen=True)
class StaminaReading:
    level: int
    current: int
    maximum: int
    confidence: float


def stamina_full_at(reading: StaminaReading, observed_at: datetime) -> datetime:
    """Conservative estimate assuming the character remains online."""
    recovery = reading.maximum // 100 + reading.level // 50 + 2
    ticks = (max(0, reading.maximum - reading.current) + recovery - 1) // recovery
    return observed_at + timedelta(minutes=ticks * 5)


class StaminaDetector:
    def __init__(self, recognizer=None) -> None:
        self.recognizer = recognizer or recognize_shared_text
        self.cache: OrderedDict[tuple[str, bytes], tuple[str, float] | None] = OrderedDict()
        self.retry_after = 0.0

    @staticmethod
    def _field(image: np.ndarray, panel: PanelObservation,
               rect: tuple[int, int, int, int]) -> np.ndarray | None:
        if panel.bbox is None:
            return None
        x, y, right, _ = panel.bbox
        scale = (right - x) / 63
        if scale <= 0:
            return None
        left = round(x + rect[0] * scale)
        top = round(y + rect[1] * scale)
        r = round(x + rect[2] * scale)
        bottom = round(y + rect[3] * scale)
        if left < 0 or top < 0 or r > image.shape[1] or bottom > image.shape[0] or r <= left or bottom <= top:
            return None
        return cv2.resize(image[top:bottom, left:r], (rect[2] - rect[0], rect[3] - rect[1]),
                          interpolation=cv2.INTER_AREA)

    def _read(self, field: np.ndarray, kind: str) -> tuple[str, float] | None:
        key = (kind, field.tobytes())
        if key in self.cache:
            self.cache.move_to_end(key)
            return self.cache[key]
        gray = cv2.cvtColor(field, cv2.COLOR_BGR2GRAY)
        candidates: list[tuple[str, float]] = []
        for threshold in ((90, 110, 130) if kind == "label" else (70, 90, 110, 130, 150)):
            mask = (gray < threshold).astype(np.uint8) * 255
            result = self.recognizer(mask)
            if result is None:
                continue
            value, confidence = result
            if kind == "label" and value != "体力":
                continue
            if kind == "level" and not re.fullmatch(r"[0-9]{1,3}", value):
                continue
            if kind == "stamina" and not re.fullmatch(r"[0-9]{1,5}/[0-9]{1,5}", value):
                continue
            candidates.append((value, confidence))
        counts = Counter(value for value, _ in candidates)
        winners = [value for value, count in counts.items() if count >= 2]
        result = (winners[0], min(score for value, score in candidates if value == winners[0])) if len(winners) == 1 else None
        self.cache[key] = result
        self.cache.move_to_end(key)
        while len(self.cache) > 32:
            self.cache.popitem(last=False)
        return result

    def analyze(self, image: np.ndarray, panel: PanelObservation) -> StaminaReading | None:
        if panel.panel_type != "attributes" or panel.bbox is None or image.ndim != 3:
            return None
        if monotonic() < self.retry_after:
            return None
        label = self._field(image, panel, (36, 179, 72, 195))
        level = self._field(image, panel, (112, 32, 159, 48))
        stamina = self._field(image, panel, (75, 180, 152, 194))
        if label is None or level is None or stamina is None:
            return None
        try:
            if self._read(label, "label") is None:
                return None
            level_text = self._read(level, "level")
            stamina_text = self._read(stamina, "stamina")
        except Exception:  # noqa: BLE001 - OCR faults must not interrupt window monitoring
            self.retry_after = monotonic() + 30
            return None
        if level_text is None or stamina_text is None:
            return None
        rank = int(level_text[0])
        current, maximum = map(int, stamina_text[0].split("/"))
        if not 1 <= rank <= 175 or not 1 <= maximum <= 99999 or not 0 <= current <= maximum:
            return None
        return StaminaReading(rank, current, maximum, min(level_text[1], stamina_text[1]))


class StaminaTracker:
    """Require consecutive matching frames; keep the last observation after the panel closes."""

    def __init__(self) -> None:
        self.pending: StaminaReading | None = None
        self.count = 0
        self.last_frame_ms: int | None = None
        self.confirmed: dict[str, object] = {}

    def update(self, reading: StaminaReading | None, timestamp_ms: int) -> dict[str, object]:
        if (reading is None or self.last_frame_ms is not None
                and (timestamp_ms <= self.last_frame_ms or timestamp_ms - self.last_frame_ms > 5000)):
            self.pending, self.count = None, 0
        self.last_frame_ms = timestamp_ms
        if reading is not None:
            same = (self.pending is not None and
                    (reading.level, reading.current, reading.maximum) ==
                    (self.pending.level, self.pending.current, self.pending.maximum))
            self.count = self.count + 1 if same else 1
            self.pending = reading
            if self.count >= 2 and (self.confirmed.get("stamina_current") != reading.current
                                    or self.confirmed.get("stamina_max") != reading.maximum
                                    or self.confirmed.get("character_level") != reading.level):
                self.confirmed = {
                    "stamina_current": reading.current,
                    "stamina_max": reading.maximum,
                    "character_level": reading.level,
                    "stamina_observed_at": datetime.now(UTC).isoformat(timespec="milliseconds"),
                }
        return dict(self.confirmed)
