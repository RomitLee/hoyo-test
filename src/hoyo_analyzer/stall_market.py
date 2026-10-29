"""Read first-level gem bids from another player's buying stall."""

from __future__ import annotations

import hashlib
import re
from collections import Counter, OrderedDict
from dataclasses import dataclass, field
from datetime import UTC, datetime
from uuid import uuid4

import cv2
import numpy as np

from .models import Event
from .panel_targets import recognize_shared_text
from .panels import PanelObservation

GEM_NAMES = ("光芒石", "月亮石", "太阳石", "红玛瑙", "五色灵尘", "星辉石", "黑宝石", "舍利子")
_NAME_PATTERN = re.compile(r"1级(" + "|".join(GEM_NAMES) + r")")
_TEMPLATE_RECT = (19, 81, 148, 104)


@dataclass(frozen=True)
class StallMarketReading:
    owner_id: str
    quotes: tuple[tuple[str, int], ...]
    confidence: float = field(compare=False)


class StallMarketDetector:
    def __init__(self, recognizer=None) -> None:
        self.recognizer = recognizer or recognize_shared_text
        self.cache: OrderedDict[bytes, StallMarketReading | None] = OrderedDict()

    @staticmethod
    def _crop(image: np.ndarray, panel: PanelObservation,
              rect: tuple[int, int, int, int]) -> np.ndarray | None:
        if panel.bbox is None:
            return None
        x, y, right, _ = panel.bbox
        scale = (right - x) / (_TEMPLATE_RECT[2] - _TEMPLATE_RECT[0])
        if scale <= 0:
            return None
        left = round(x + (rect[0] - _TEMPLATE_RECT[0]) * scale)
        top = round(y + (rect[1] - _TEMPLATE_RECT[1]) * scale)
        r = round(x + (rect[2] - _TEMPLATE_RECT[0]) * scale)
        bottom = round(y + (rect[3] - _TEMPLATE_RECT[1]) * scale)
        if left < 0 or top < 0 or r > image.shape[1] or bottom > image.shape[0] or r <= left or bottom <= top:
            return None
        return cv2.resize(image[top:bottom, left:r], (rect[2] - rect[0], rect[3] - rect[1]),
                          interpolation=cv2.INTER_AREA)

    def _vote(self, field: np.ndarray, kind: str) -> tuple[str, float] | None:
        gray = cv2.cvtColor(field, cv2.COLOR_BGR2GRAY)
        candidates: list[tuple[str, float]] = []
        for threshold in ((80, 95, 110, 125, 140) if kind == "id" else
                          (105, 135, 165, 190) if kind == "name" else (25, 35, 45, 55, 65)):
            if kind == "id":
                mask = gray < threshold
            elif kind == "name":
                mask = gray > threshold
            else:
                background = np.median(field[:, -8:], axis=(0, 1))
                mask = np.max(np.abs(field.astype(np.int16) - background.astype(np.int16)), axis=2) > threshold
            result = self.recognizer(mask.astype(np.uint8) * 255)
            if result is None:
                continue
            value, confidence = result
            valid = (re.fullmatch(r"[0-9]{6,12}", value) if kind == "id" else
                     _NAME_PATTERN.fullmatch(value) if kind == "name" else
                     re.fullmatch(r"[1-9][0-9]{0,8}", value))
            if valid and confidence >= .75:
                candidates.append((value, confidence))
        counts = Counter(value for value, _ in candidates)
        winners = [value for value, count in counts.items() if count >= 2]
        if len(winners) == 1:
            winner = winners[0]
        else:
            ranked = counts.most_common()
            if not ranked or len(ranked) > 1 and ranked[0][1] == ranked[1][1]:
                return None
            winner = ranked[0][0]
        return winner, min(score for value, score in candidates if value == winner)

    def analyze(self, image: np.ndarray, panel: PanelObservation) -> StallMarketReading | None:
        if panel.panel_type != "stall_other_buy" or panel.bbox is None or image.ndim != 3:
            return None
        # Ignore video background and the cursor. Only the owner and bid list affect the result.
        owner_field = self._crop(image, panel, (178, 53, 259, 72))
        list_field = self._crop(image, panel, (75, 118, 339, 399))
        if owner_field is None or list_field is None:
            return None
        key = hashlib.sha256(owner_field.tobytes() + list_field.tobytes()).digest()
        if key in self.cache:
            self.cache.move_to_end(key)
            return self.cache[key]
        try:
            owner = self._vote(owner_field, "id")
            if owner is None:
                result = None
            else:
                quotes: dict[str, tuple[int, float]] = {}
                ambiguous: set[str] = set()
                for row in range(5):
                    dy = 58 * row
                    for name_rect, price_rect in (((78, 119 + dy, 169, 139 + dy),
                                                   (114, 148 + dy, 176, 162 + dy)),
                                                  ((238, 119 + dy, 330, 139 + dy),
                                                   (274, 148 + dy, 336, 162 + dy))):
                        name_field = self._crop(image, panel, name_rect)
                        if name_field is None:
                            continue
                        name = self._vote(name_field, "name")
                        if name is None:
                            continue
                        gem = name[0][2:]
                        price_field = self._crop(image, panel, price_rect)
                        price = self._vote(price_field, "price") if price_field is not None else None
                        if price is None:
                            continue
                        amount = int(price[0])
                        if gem in quotes and quotes[gem][0] != amount:
                            ambiguous.add(gem)
                        else:
                            quotes[gem] = amount, min(name[1], price[1])
                for gem in ambiguous:
                    quotes.pop(gem, None)
                result = (StallMarketReading(owner[0], tuple(sorted((name, price) for name, (price, _) in quotes.items())),
                                             min([owner[1], *(confidence for _, confidence in quotes.values())]))
                          if quotes else None)
        except Exception:  # noqa: BLE001 - optional market OCR cannot interrupt monitoring
            result = None
        self.cache[key] = result
        self.cache.move_to_end(key)
        while len(self.cache) > 32:
            self.cache.popitem(last=False)
        return result


class StallMarketTracker:
    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self.pending: StallMarketReading | None = None
        self.count = 0
        self.emitted: StallMarketReading | None = None
        self.last_ms: int | None = None

    def update(self, reading: StallMarketReading | None, timestamp_ms: int, frame_index: int = 0) -> list[Event]:
        if reading is None or (self.last_ms is not None and
                               (timestamp_ms <= self.last_ms or timestamp_ms - self.last_ms > 5000)):
            self.pending, self.count = None, 0
        self.last_ms = timestamp_ms
        if reading is None:
            return []
        self.count = self.count + 1 if reading == self.pending else 1
        self.pending = reading
        if self.count < 2 or reading == self.emitted:
            return []
        self.emitted = reading
        now = datetime.now(UTC)
        return [Event(uuid4().hex, timestamp_ms, "market_gem_quotes", confidence=reading.confidence,
                      payload={"stall_owner_id": reading.owner_id,
                               "quotes": [{"gem_name": name, "price_coins": price} for name, price in reading.quotes],
                               "occurred_at": now.isoformat(timespec="milliseconds")},
                      evidence_frame_index=frame_index)]
