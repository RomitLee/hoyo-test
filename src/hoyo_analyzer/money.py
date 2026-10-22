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
        background_field = field
        # Twelve digits occupy at most 84 pixels in this game font. A cursor
        # over the far right of the input must not invalidate readable numbers.
        field = field[:, :100]
        reference = np.array((190, 182, 174), dtype=np.int16)
        # Reject colourful overlays/cursors instead of silently reading a prefix.
        if np.any(np.ptp(field.astype(np.int16), axis=2) > 55):
            return None
        if float((np.abs(background_field.astype(np.int16) - reference).max(axis=2) < 35).mean()) < 0.75:
            return None
        gray = cv2.cvtColor(field, cv2.COLOR_BGR2GRAY)
        values = {value for threshold in (85, 100, 115, 130, 145)
                  if (value := self._read_mask((gray < threshold).astype(np.uint8))) is not None}
        return values.pop() if len(values) == 1 else None

    def _read_mask(self, mask: np.ndarray) -> int | None:
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

    def _read_near(self, image: np.ndarray, x: float, y: float, scale: float) -> int | None:
        # Title rounding at 125% can shift the distant money row by several
        # pixels. Search only a small neighbourhood, never the whole screen.
        candidates = set()
        for dx, dy in [(0, 0)] + [(dx, dy) for dy in (0, -1, 1, -2, 2, -3, 3, -4, 4)
                                   for dx in (-2, -1, 0, 1, 2) if (dx, dy) != (0, 0)]:
            left, top = round(x) + dx, round(y) + dy
            right, bottom = left + round(148 * scale), top + round(14 * scale)
            if left < 0 or top < 0 or right > image.shape[1] or bottom > image.shape[0]:
                continue
            field = cv2.resize(image[top:bottom, left:right], (148, 14), interpolation=cv2.INTER_AREA)
            value = self._read(field)
            if value is not None:
                if (dx, dy) == (0, 0):
                    return value
                candidates.add(value)
        return candidates.pop() if len(candidates) == 1 else None

    def read_fields(
        self, image: np.ndarray, title_bbox: tuple[int, int, int, int] | None,
        grid_bbox: tuple[int, int, int, int] | None = None,
    ) -> tuple[int | None, int | None]:
        self.reason = "背包金额区域未定位"
        anchors = []
        if title_bbox is not None:
            x, y, right, _ = title_bbox
            scale = (right - x) / 67
            anchors.append((x + 74 * scale, y + 273 * scale, scale))
        elif grid_bbox is not None:
            left, top, right, bottom = grid_bbox
            scale = (right - left) / 255
            if scale > 0 and abs((bottom - top) / scale - 204) < 12:
                anchors.append((left + 56 * scale, bottom + 13 * scale, scale))
        cash = bank = None
        for left, top, scale in anchors:
            if scale <= 0:
                continue
            cash = self._read_near(image, left, top, scale)
        self.reason = "现金可读" if cash is not None else ("数字字形或金额背景不匹配" if anchors else self.reason)
        return cash, bank

    def analyze(self, image: np.ndarray, title_bbox: tuple[int, int, int, int] | None) -> tuple[int, int] | None:
        cash, bank = self.read_fields(image, title_bbox)
        return (cash, bank) if cash is not None else None


class MoneyTracker:
    """Confirm cash only; retain the last confirmed value."""

    def __init__(self) -> None:
        self.last_frame_ms: int | None = None
        self.confirmed: dict[str, object] = {}
        self.reset_pending()

    def reset_pending(self) -> None:
        self.pending: list[int | None] = [None, None]
        self.count = [0, 0]

    def update(self, value: tuple[int | None, int | None] | None, timestamp_ms: int) -> dict[str, object]:
        if self.last_frame_ms is not None and (timestamp_ms - self.last_frame_ms > 5000 or timestamp_ms <= self.last_frame_ms):
            self.reset_pending()
        self.last_frame_ms = timestamp_ms
        readable = []
        for index, (key, amount) in enumerate(zip(("cash",), (value or (None, None))[:1], strict=True)):
            if amount is None:
                self.pending[index], self.count[index] = None, 0
            else:
                self.count[index] = self.count[index] + 1 if self.pending[index] == amount else 1
                self.pending[index] = amount
                if self.count[index] >= 3:
                    self.confirmed[key] = amount
                    self.confirmed["money_confirmed_at"] = datetime.now(UTC).isoformat(timespec="milliseconds")
                    if key == "cash":
                        self.confirmed["cash_confirmed_at"] = self.confirmed["money_confirmed_at"]
            readable.append(amount is not None and self.count[index] >= 3)
        return {**self.confirmed, "money_readable": all(readable), "cash_readable": readable[0]}
