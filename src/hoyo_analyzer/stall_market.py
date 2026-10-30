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

from .models import BEIJING_TZ, Event
from .money import MoneyDetector
from .panel_targets import recognize_shared_text
from .panels import PanelObservation

GEM_NAMES = ("光芒石", "月亮石", "太阳石", "红玛瑙", "五色灵尘", "星辉石", "黑宝石", "舍利子")
_NAME_PATTERN = re.compile(r"1级(" + "|".join(GEM_NAMES) + r")")
_MISSING_LEVEL_PATTERN = re.compile(r"级(" + "|".join(GEM_NAMES) + r")")
_TEMPLATE_RECT = (19, 81, 148, 104)
_PRICE_THRESHOLDS = range(88, 106)



def _normalize_mask(mask: np.ndarray) -> np.ndarray:
    """The OCR model expects dark glyphs on a light page.

    Some stall skins draw white text on a dark plate; flipping a mostly-dark
    mask keeps the recognizer working on both layouts.
    """
    return 255 - mask if mask.mean() < 127 else mask

def _price_mask(field: np.ndarray, threshold: int) -> np.ndarray:
    """Highlight digits against the dominant stall-plate colour.

    The plate is a flat colour. Sampling the rightmost columns as "background"
    breaks down whenever a scrollbar, a gem icon or the mouse cursor overlaps
    that strip, which silently dropped whole rows from the reading. The mode of
    the cell is stable in all of those cases.
    """
    quantized = (field // 8).reshape(-1, 3)
    colors, counts = np.unique(quantized, axis=0, return_counts=True)
    background = colors[int(counts.argmax())] * 8 + 4
    distance = np.max(np.abs(field.astype(np.int16) - background.astype(np.int16)), axis=2)
    return (distance > threshold).astype(np.uint8) * 255


def _normalize_gem_name(value: str) -> str | None:
    """Repair only the common dropped leading ``1`` on first-level gems."""
    value = value.strip(" ，。,.·、■□")
    if _NAME_PATTERN.fullmatch(value):
        return value
    if _MISSING_LEVEL_PATTERN.fullmatch(value):
        return "1" + value
    return None


@dataclass(frozen=True)
class StallMarketReading:
    owner_id: str
    quotes: tuple[tuple[str, int], ...]
    confidence: float = field(compare=False)


@dataclass(frozen=True)
class OwnStallReading:
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

    def _vote(self, field: np.ndarray, kind: str, dark_skin: bool = False) -> tuple[str, float] | None:
        gray = cv2.cvtColor(field, cv2.COLOR_BGR2GRAY)
        candidates: list[tuple[str, float]] = []
        for threshold in ((80, 95, 105, 115, 125, 135, 140) if kind == "id" else
                          (105, 135, 165, 190) if kind == "name" else
                          (50, 60, 70, 80) if dark_skin else (25, 35, 45, 55, 65)):
            masks: list[np.ndarray] = []
            if kind == "id":
                # Owner plates vary: dark digits on light, or the reverse.
                masks = [(gray < threshold).astype(np.uint8) * 255,
                         (gray > threshold).astype(np.uint8) * 255]
            elif kind == "name":
                masks = [(((gray < threshold) if dark_skin else (gray > threshold))
                          .astype(np.uint8) * 255)]
            else:
                masks = [_price_mask(field, threshold)]
            result = None
            for mask in masks:
                result = self.recognizer(mask if dark_skin else _normalize_mask(mask))
                if result:
                    break
            if result is None:
                continue
            value, confidence = result
            valid = (re.fullmatch(r"[0-9]{6,12}", value) if kind == "id" else
                     _NAME_PATTERN.fullmatch(value) if kind == "name" else
                     re.fullmatch(r"[1-9][0-9]{0,8}", value))
            if valid and confidence >= .75:
                candidates.append((value, confidence))
        # Prefer readings the model itself is confident about; low-confidence
        # variants often add or drop a digit and would create phantom merchants.
        strong = [(value, score) for value, score in candidates if score >= .85]
        pool = strong or candidates
        counts = Counter(value for value, _ in pool)
        winners = [value for value, count in counts.items() if count >= 2]
        if len(winners) == 1:
            winner = winners[0]
        else:
            ranked = counts.most_common()
            if not ranked:
                return None
            if len(ranked) > 1 and ranked[0][1] == ranked[1][1]:
                # Same support: prefer the clearer reading instead of dropping
                # the whole stall (owner ids are required for pricing).
                scores = {value: max(score for candidate, score in pool if candidate == value)
                          for value, _ in ranked}
                best, second = ranked[0][0], ranked[1][0]
                if abs(scores[best] - scores[second]) < 0.05:
                    return None
                winner = best if scores[best] > scores[second] else second
            else:
                winner = ranked[0][0]
        return winner, min(score for value, score in pool if value == winner)

    def analyze(self, image: np.ndarray, panel: PanelObservation) -> StallMarketReading | None:
        if panel.panel_type != "stall_other_buy" or panel.bbox is None or image.ndim != 3:
            return None
        # Ignore video background and the cursor. Only the owner and bid list affect the result.
        owner_field = self._crop(image, panel, (178, 53, 259, 72))
        list_field = self._crop(image, panel, (75, 118, 339, 399))
        if owner_field is None or list_field is None:
            return None
        # Two stall skins exist: light rows (dark text) and dark rows (white text).
        dark_skin = float(np.median(cv2.cvtColor(list_field, cv2.COLOR_BGR2GRAY))) < 90
        key = hashlib.sha256(owner_field.tobytes() + list_field.tobytes()).digest()
        if key in self.cache:
            self.cache.move_to_end(key)
            return self.cache[key]
        try:
            owner = self._vote(owner_field, "id")
            owner_id = owner[0] if owner is not None else ""
            owner_confidence = owner[1] if owner is not None else 0.0
            if True:
                quotes: dict[str, tuple[int, float]] = {}
                ambiguous: set[str] = set()
                for row in range(5):
                    dy = 58 * row
                    for name_rect, price_rect in (((78, 119 + dy, 169, 139 + dy),
                                                   (114, 148 + dy, 176, 162 + dy)),
                                                  ((238, 119 + dy, 330, 139 + dy),
                                                   (274, 148 + dy, 336, 162 + dy))):
                        if dark_skin:
                            # Leave a margin so the leading "1级" is not clipped.
                            name_rect = (name_rect[0] - 4, name_rect[1], name_rect[2], name_rect[3])
                        # The dark skin drifts a couple of pixels per row; try
                        # small offsets so the last rows keep their level prefix.
                        name = None
                        price = None
                        for offset in ((0, 2, 4) if dark_skin else (0,)):
                            shifted_name = (name_rect[0], name_rect[1] + offset,
                                            name_rect[2], name_rect[3] + offset)
                            shifted_price = (price_rect[0], price_rect[1] + offset,
                                             price_rect[2], price_rect[3] + offset)
                            name_field = self._crop(image, panel, shifted_name)
                            price_field = self._crop(image, panel, shifted_price)
                            if name_field is None or price_field is None:
                                continue
                            name = self._vote(name_field, "name", dark_skin)
                            price = self._vote(price_field, "price", dark_skin)
                            if name is not None and price is not None:
                                break
                        if name is None or price is None:
                            continue
                        gem = name[0][2:]
                        amount = int(price[0])
                        if gem in quotes and quotes[gem][0] != amount:
                            ambiguous.add(gem)
                        else:
                            quotes[gem] = amount, min(name[1], price[1])
                for gem in ambiguous:
                    quotes.pop(gem, None)
                result = (StallMarketReading(owner_id, tuple(sorted((name, price) for name, (price, _) in quotes.items())),
                                             min([owner_confidence, *(confidence for _, confidence in quotes.values())]))
                          if quotes else None)
        except Exception:  # noqa: BLE001 - optional market OCR cannot interrupt monitoring
            result = None
        self.cache[key] = result
        self.cache.move_to_end(key)
        while len(self.cache) > 32:
            self.cache.popitem(last=False)
        return result


class OwnStallMarketDetector:
    """Read visible first-level gem bids from the player's own buying stall."""

    def __init__(self, recognizer=None) -> None:
        self.recognizer = recognizer or recognize_shared_text
        self.digits = MoneyDetector().digits

    @staticmethod
    def _crop(image: np.ndarray, panel: PanelObservation,
              rect: tuple[int, int, int, int]) -> np.ndarray | None:
        if panel.bbox is None:
            return None
        x, y, right, _ = panel.bbox
        scale = (right - x) / 94
        left = round(x + rect[0] * scale)
        top = round(y + rect[1] * scale)
        r = round(x + rect[2] * scale)
        bottom = round(y + rect[3] * scale)
        if left < 0 or top < 0 or r > image.shape[1] or bottom > image.shape[0] or r <= left or bottom <= top:
            return None
        return cv2.resize(image[top:bottom, left:r], (rect[2] - rect[0], rect[3] - rect[1]),
                          interpolation=cv2.INTER_AREA)

    def _decode_price_mask(self, mask: np.ndarray) -> tuple[int, float] | None:
        boxes = sorted(
            (cv2.boundingRect(contour)
             for contour in cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)[0]),
            key=lambda box: box[0],
        )
        # The stall's narrow scrollbar is 8-10 pixels wide and spans the full
        # cell height. Actual digits are 8-10 pixels high and at most 7 wide.
        boxes = [box for box in boxes if 8 <= box[3] <= 10 and 2 <= box[2] <= 7]
        if not 1 <= len(boxes) <= 9:
            return None
        text = ""
        scores: list[float] = []
        previous_right = None
        for x, y, width, height in boxes:
            if previous_right is not None and not 1 <= x - previous_right <= 4:
                return None
            previous_right = x + width
            glyph = mask[y:y + height, x:x + width] > 0
            candidates: list[tuple[float, str]] = []
            for digit, template in self.digits.items():
                if abs(template.shape[1] - width) > 1:
                    continue
                resized = cv2.resize(
                    template, (width, height), interpolation=cv2.INTER_NEAREST,
                ).astype(bool)
                intersection = np.logical_and(glyph, resized).sum()
                union = np.logical_or(glyph, resized).sum()
                candidates.append((float(intersection / max(1, union)), digit))
            candidates.sort(reverse=True)
            if not candidates or candidates[0][0] < .75:
                return None
            scores.append(candidates[0][0])
            text += candidates[0][1]
        if len(text) > 1 and text.startswith("0"):
            return None
        return int(text), min(scores)

    def _decode_price(self, field: np.ndarray) -> tuple[int, float] | None:
        candidates: list[tuple[int, float]] = []
        for threshold in _PRICE_THRESHOLDS:
            decoded = self._decode_price_mask(_price_mask(field, threshold))
            if decoded is not None:
                candidates.append(decoded)
        counts = Counter(value for value, _ in candidates)
        ranked = counts.most_common()
        if not ranked or ranked[0][1] < 2:
            return None
        if len(ranked) > 1 and ranked[0][1] == ranked[1][1]:
            return None
        winner = ranked[0][0]
        return winner, min(score for value, score in candidates if value == winner)

    def _vote(self, field: np.ndarray, kind: str) -> tuple[str, float] | None:
        if kind == "price":
            decoded = self._decode_price(field)
            if decoded is not None:
                return str(decoded[0]), decoded[1]
        gray = cv2.cvtColor(field, cv2.COLOR_BGR2GRAY)
        candidates: list[tuple[str, float]] = []
        thresholds = ((70, 85, 100, 115, 130, 145, 160) if kind == "name"
                      else (30, 40, 50, 60, 70, 80))
        for threshold in thresholds:
            if kind == "price":
                result = self.recognizer(_normalize_mask(_price_mask(field, threshold)))
            else:
                result = self.recognizer(_normalize_mask((gray < threshold).astype(np.uint8) * 255))
            if result is None:
                continue
            value, confidence = result
            if kind == "name":
                value = _normalize_gem_name(value)
            valid = (value is not None if kind == "name" else
                     re.fullmatch(r"[1-9][0-9]{0,8}", value))
            if valid and confidence >= .75:
                candidates.append((value, confidence))
        counts = Counter(value for value, _ in candidates)
        ranked = counts.most_common()
        if not ranked or len(ranked) > 1 and ranked[0][1] == ranked[1][1]:
            return None
        winner = ranked[0][0]
        winner_scores = [score for value, score in candidates if value == winner]
        if ranked[0][1] < 2 and max(winner_scores) < .95:
            return None
        return winner, min(winner_scores)

    def analyze(self, image: np.ndarray, panel: PanelObservation) -> OwnStallReading | None:
        if panel.panel_type != "stall_buy" or panel.bbox is None or image.ndim != 3:
            return None
        quotes: dict[str, tuple[int, float]] = {}
        ambiguous: set[str] = set()
        try:
            for row in range(5):
                dy = row * 60
                for name_rect, price_rect in (((-205, 91 + dy, -103, 113 + dy),
                                                (-201, 121 + dy, -105, 141 + dy)),
                                               ((-49, 91 + dy, 58, 113 + dy),
                                                (-49, 121 + dy, 56, 141 + dy))):
                    name_field = self._crop(image, panel, name_rect)
                    price_field = self._crop(image, panel, price_rect)
                    if name_field is None or price_field is None:
                        continue
                    name = self._vote(name_field, "name")
                    price = self._vote(price_field, "price")
                    if name is None or price is None:
                        continue
                    gem = name[0][2:]
                    amount = int(price[0])
                    if gem in quotes and quotes[gem][0] != amount:
                        ambiguous.add(gem)
                    else:
                        quotes[gem] = amount, min(name[1], price[1])
        except Exception:  # noqa: BLE001 - optional OCR cannot interrupt monitoring
            return None
        for gem in ambiguous:
            quotes.pop(gem, None)
        if not quotes:
            return None
        return OwnStallReading(
            tuple(sorted((name, price) for name, (price, _) in quotes.items())),
            min(confidence for _, confidence in quotes.values()),
        )


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


class StallVisitTracker:
    """Detect every close-then-open visit to another merchant's buying stall."""

    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self.pending: bytes | None = None
        self.count = 0
        self.emitted: bytes | None = None
        self.reopen_pending = False

    @staticmethod
    def _same(left: bytes | None, right: bytes | None) -> bool:
        if left is None or right is None:
            return left is right
        return sum((a ^ b).bit_count() for a, b in zip(left, right)) <= 6

    @staticmethod
    def signature(detector: StallMarketDetector, image: np.ndarray, panel: PanelObservation) -> bytes | None:
        if panel.panel_type != "stall_other_buy":
            return None
        field = detector._crop(image, panel, (50, 53, 259, 72))
        if field is None:
            return None
        gray = cv2.cvtColor(field, cv2.COLOR_BGR2GRAY)
        small = cv2.resize(gray, (33, 8), interpolation=cv2.INTER_AREA)
        return np.packbits(small[:, 1:] > small[:, :-1]).tobytes()

    def update(self, signature: bytes | None, timestamp_ms: int, frame_index: int = 0) -> list[Event]:
        if signature is None:
            # Closing a stall can be shorter than PanelTracker's generic
            # default-screen confirmation window.  Preserve the last merchant
            # across that real absence so reopening another stall cannot be
            # mistaken for the original continuously open panel.
            if self.emitted is not None:
                self.reopen_pending = True
            self.pending, self.count = None, 0
            return []
        if self._same(signature, self.pending):
            self.count += 1
        else:
            self.pending, self.count = signature, 1
        required = 3 if self.reopen_pending else 2
        if self.count < required:
            return []
        first_visit = self.emitted is None
        same_merchant = self._same(signature, self.emitted)
        self.emitted = signature
        was_reopened = self.reopen_pending
        self.reopen_pending = False
        if first_visit or (same_merchant and not was_reopened):
            return []
        now = datetime.now(BEIJING_TZ)
        return [Event(uuid4().hex, timestamp_ms, "market_stall_changed", confidence=.9,
                      payload={"panel_type": "stall_other_buy", "message": "打开他人收货摊位",
                               "occurred_at": now.isoformat(timespec="milliseconds")},
                      evidence_frame_index=frame_index,
                      beijing_time=now.strftime("%Y年%m月%d日%H:%M:%S"))]
