"""Conservative local digit recognition for the inventory cash fields."""

from __future__ import annotations

import json
from datetime import UTC, datetime

import cv2
import numpy as np

from .paths import resolve_application_path


class MoneyDetector:
    def __init__(self) -> None:
        data = json.loads(resolve_application_path("assets/templates/money_digits.json").read_text(encoding="utf-8"))
        self.digits = {key: np.array(value, dtype=np.uint8) for key, value in data["digits"].items()}

    def _read(self, field: np.ndarray) -> int | None:
        # Field includes the flat grey background, but excludes its rounded edges.
        if field.shape != (14, 148, 3):
            return None
        reference = np.array((190, 182, 174), dtype=np.int16)
        # Reject colourful overlays/cursors instead of silently reading a prefix.
        if np.any(np.ptp(field.astype(np.int16), axis=2) > 55):
            return None
        if float((np.abs(field.astype(np.int16) - reference).max(axis=2) < 35).mean()) < 0.75:
            return None
        mask = (cv2.cvtColor(field, cv2.COLOR_BGR2GRAY) < 100).astype(np.uint8)
        boxes = sorted(cv2.boundingRect(c) for c in cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)[0])
        if not 1 <= len(boxes) <= 12 or boxes[0][0] > 5:
            return None
        text = ""
        previous_right = None
        for x, y, width, height in boxes:
            if height not in (8, 9, 10) or width < 2 or width > 8 or y == 0 or y + height >= 14:
                return None
            if previous_right is not None and not 1 <= x - previous_right <= 4:
                return None
            previous_right = x + width
            glyph = mask[y:y + height, x:x + width]
            scores = []
            for digit, template in self.digits.items():
                if abs(template.shape[1] - width) > 1:
                    continue
                resized = cv2.resize(template, (width, height), interpolation=cv2.INTER_NEAREST)
                score = float(np.logical_and(glyph, resized).sum() / max(1, np.logical_or(glyph, resized).sum()))
                scores.append((score, digit))
            scores.sort(reverse=True)
            if not scores or scores[0][0] < 0.82 or (len(scores) > 1 and scores[0][0] - scores[1][0] < 0.12):
                return None
            text += scores[0][1]
        if len(text) > 1 and text.startswith("0"):
            return None
        return int(text)

    def analyze(self, image: np.ndarray, title_bbox: tuple[int, int, int, int] | None) -> tuple[int, int] | None:
        if title_bbox is None:
            return None
        x, y, right, _ = title_bbox
        scale = (right - x) / 67
        values = []
        for offset_y in (273, 300):
            left, top = round(x + 74 * scale), round(y + offset_y * scale)
            end_x, end_y = round(x + 222 * scale), round(y + (offset_y + 14) * scale)
            if left < 0 or top < 0 or end_x > image.shape[1] or end_y > image.shape[0]:
                return None
            field = cv2.resize(image[top:end_y, left:end_x], (148, 14), interpolation=cv2.INTER_AREA)
            value = self._read(field)
            if value is None:
                return None
            values.append(value)
        return values[0], values[1]


class MoneyTracker:
    """Keep last confirmed values; unknown observations break consecutive evidence."""

    def __init__(self) -> None:
        self.pending: tuple[int, int] | None = None
        self.count = 0
        self.last_frame_ms: int | None = None
        self.confirmed: dict[str, object] = {}

    def update(self, value: tuple[int, int] | None, timestamp_ms: int) -> dict[str, object]:
        if self.last_frame_ms is not None and timestamp_ms - self.last_frame_ms > 1500:
            self.pending, self.count = None, 0
        self.last_frame_ms = timestamp_ms
        if value is None:
            self.pending, self.count = None, 0
        else:
            self.count = self.count + 1 if self.pending == value else 1
            self.pending = value
            if self.count >= 3:
                self.confirmed = {"cash": value[0], "bank_money": value[1],
                                  "money_confirmed_at": datetime.now(UTC).isoformat(timespec="milliseconds")}
        return {**self.confirmed, "money_readable": value is not None and self.count >= 3}
