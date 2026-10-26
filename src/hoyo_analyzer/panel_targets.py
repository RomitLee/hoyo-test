"""Offline recipient OCR, anchored to the detected dialog rather than the desktop."""

from __future__ import annotations

import hashlib
import math
from collections import Counter, OrderedDict
from dataclasses import dataclass
from datetime import datetime
from threading import Lock
from time import monotonic
from uuid import uuid4

import cv2
import numpy as np

from .models import BEIJING_TZ, Event
from .panels import PanelObservation
from .paths import resolve_application_path

MODEL_SHA256 = "48fc40f24f6d2a207a2b1091d3437eb3cc3eb6b676dc3ef9c37384005483683b"
TARGET_PANELS = {"give", "trade"}
_engine_lock = Lock()
_engine = None


@dataclass(frozen=True)
class TargetReading:
    name: str | None = None
    level: int | None = None
    confidence: float = 0.0
    reason: str = "对象未识别"

    @property
    def identity(self) -> tuple[str, int] | None:
        return (self.name, self.level) if self.name is not None and self.level is not None else None


class LocalTextRecognizer:
    """One CPU session shared by all windows; models are never downloaded at runtime."""

    def __init__(self) -> None:
        import onnxruntime as ort

        path = resolve_application_path("assets/ocr/ch_PP-OCRv4_rec_mobile.onnx")
        if hashlib.sha256(path.read_bytes()).hexdigest() != MODEL_SHA256:
            raise ValueError("对象文字识别模型损坏")
        options = ort.SessionOptions()
        options.intra_op_num_threads = options.inter_op_num_threads = 1
        self.session = ort.InferenceSession(str(path), sess_options=options, providers=["CPUExecutionProvider"])
        self.input_name = self.session.get_inputs()[0].name
        self.characters = [""] + self.session.get_modelmeta().custom_metadata_map["character"].splitlines() + [" "]

    def recognize(self, mask: np.ndarray) -> tuple[str, float] | None:
        yy, xx = np.where(mask > 0)
        if not len(xx):
            return None
        glyphs = 255 - mask[yy.min():yy.max() + 1, xx.min():xx.max() + 1]
        # Agree across different padding, without a player-name dictionary or
        # simplified/traditional conversion that would change someone's name.
        candidates = []
        for padding in (3, 4, 6, 8):
            line = cv2.copyMakeBorder(glyphs, padding, padding, padding, padding, cv2.BORDER_CONSTANT, value=255)
            width = math.ceil(48 * line.shape[1] / line.shape[0])
            tensor = np.zeros((1, 3, 48, max(320, width)), dtype=np.float32)
            pixels = cv2.resize(line, (width, 48)).astype(np.float32) / 127.5 - 1
            tensor[0, :, :, :width] = pixels[None, :, :]
            probabilities = self.session.run(None, {self.input_name: tensor})[0][0]
            indices = probabilities.argmax(axis=1)
            keep = np.r_[True, indices[1:] != indices[:-1]] & (indices != 0)
            scores = probabilities.max(axis=1)[keep]
            text = "".join(self.characters[index] for index in indices[keep]).strip()
            if text and scores.size and scores.min() >= 0.75 and scores.mean() >= 0.88:
                candidates.append((text, float(scores.min())))
        counts = Counter(text for text, _ in candidates)
        winners = [text for text, count in counts.items() if count >= 2]
        if len(winners) != 1:
            return None
        return winners[0], min(score for text, score in candidates if text == winners[0])


class PanelTargetDetector:
    def __init__(self, recognizer=None) -> None:
        self.recognizer = recognizer
        self.cache: OrderedDict[bytes, tuple[float, TargetReading]] = OrderedDict()
        self.error = ""
        self.retry_after = 0.0

    @staticmethod
    def _mask(image: np.ndarray, panel: PanelObservation, rect: tuple[int, int, int, int]) -> np.ndarray | None:
        x, y, right, _ = panel.bbox
        scale = (right - x) / (40 if panel.panel_type == "give" else 39)
        left, top, r, bottom = (round(origin + delta * scale) for origin, delta in zip((x, y, x, y), rect))
        if scale <= 0 or r <= left or bottom <= top or left < 0 or top < 0 or r > image.shape[1] or bottom > image.shape[0]:
            return None
        field = cv2.resize(image[top:bottom, left:r], (rect[2] - rect[0], rect[3] - rect[1]),
                           interpolation=cv2.INTER_AREA)
        blue, green, red = cv2.split(field)
        # Cursors and coloured overlays must not turn a partially covered name
        # into a different, shorter but confidently recognized recipient.
        foreign = ((blue > 140) & (green > 120) & (red < 110)) | ((red > 170) & (green < 100))
        if foreign.sum() > 2:
            return None
        mask = ((blue < 140) & (green > 165) & (red > 165)
                & (np.abs(red.astype(np.int16) - green.astype(np.int16)) < 85)).astype(np.uint8) * 255
        yy, xx = np.where(mask > 0)
        if len(xx) < 12 or xx.min() == 0 or xx.max() == mask.shape[1] - 1 or yy.min() == 0 or yy.max() == mask.shape[0] - 1:
            return None
        return mask

    def analyze(self, image: np.ndarray, panel: PanelObservation) -> TargetReading:
        if panel.panel_type not in TARGET_PANELS or panel.bbox is None:
            return TargetReading(reason="未定位给予或交易面板")
        if image.ndim != 3 or image.shape[2] != 3 or not image.size:
            return TargetReading(reason="游戏画面不可用")
        rects = ((-72, 372, 24, 392), (60, 372, 105, 392)) if panel.panel_type == "give" else (
            (-145, 165, -44, 186), (-172, 194, -135, 214))
        masks = [self._mask(image, panel, rect) for rect in rects]
        if any(mask is None for mask in masks):
            return TargetReading(reason="对象文字被遮挡或不完整")
        key = hashlib.sha256(panel.panel_type.encode() + b"".join(mask.tobytes() for mask in masks)).digest()
        now = monotonic()
        if key in self.cache:
            seen, value = self.cache[key]
            if value.identity is not None or now - seen < 1:
                self.cache.move_to_end(key)
                return value
        if now < self.retry_after:
            return TargetReading(reason=self.error)
        try:
            with _engine_lock:
                global _engine
                if self.recognizer is None:
                    if _engine is None:
                        _engine = LocalTextRecognizer()
                    recognizer = _engine
                else:
                    recognizer = self.recognizer
                name, level = (recognizer.recognize(mask) for mask in masks)
            if (name is not None and level is not None and 1 <= len(name[0]) <= 16
                    and all(c.isprintable() and not c.isspace() for c in name[0])
                    and level[0].isascii() and level[0].isdigit() and 1 <= int(level[0]) <= 175):
                value = TargetReading(name[0], int(level[0]), min(name[1], level[1]), "对象已识别")
            else:
                value = TargetReading(reason="对象文字未通过一致性校验")
        except Exception as exc:  # noqa: BLE001 - OCR must not stop capture, cash monitoring or panel detection.
            self.error = f"对象识别不可用：{type(exc).__name__}"
            self.retry_after = now + 30
            value = TargetReading(reason=self.error)
        self.cache[key] = now, value
        self.cache.move_to_end(key)
        while len(self.cache) > 32:
            self.cache.popitem(last=False)
        return value


class PanelTargetTracker:
    """Confirm the pair together and never display the preceding player's identity."""

    def __init__(self, confirm_frames: int = 3) -> None:
        self.confirm_frames = confirm_frames
        self.reset()

    def reset(self) -> None:
        self.panel = None
        self.last_ms = None
        self.candidate = None
        self.count = 0
        self.emitted = None
        self.current: TargetReading | None = None

    def update(self, panel: str, reading: TargetReading, timestamp_ms: int, frame_index: int = 0) -> list[Event]:
        if panel not in TARGET_PANELS:
            self.reset()
            return []
        if self.panel != panel or (self.last_ms is not None and (timestamp_ms <= self.last_ms or timestamp_ms - self.last_ms > 1500)):
            self.reset()
        self.panel, self.last_ms = panel, timestamp_ms
        self.current = None
        identity = reading.identity
        if identity != self.candidate:
            self.candidate, self.count = identity, 0
        self.count += 1
        if self.count < self.confirm_frames:
            return []
        self.current = reading if identity is not None else None
        if identity == self.emitted:
            return []
        self.emitted = identity
        now = datetime.now(BEIJING_TZ)
        name = "给予" if panel == "give" else "交易"
        details = f"{reading.name}，{reading.level}级" if identity is not None else "对象未识别"
        return [Event(uuid4().hex, timestamp_ms, "panel_target_changed", confidence=reading.confidence,
                      payload={"panel_type": panel, "message": f"{name}对象：{details}",
                               **target_payload(self.current), "occurred_at": now.isoformat(timespec="milliseconds")},
                      evidence_frame_index=frame_index, beijing_time=now.strftime("%Y年%m月%d日%H:%M:%S"))]


def target_payload(reading: TargetReading | None) -> dict:
    return {"target_name": reading.name if reading else None, "target_level": reading.level if reading else None,
            "target_confidence": reading.confidence if reading else None,
            "target_status": "recognized" if reading else "unrecognized"}


def attach_target(event: Event, reading: TargetReading | None) -> None:
    event.payload.update(target_payload(reading))
    detail = f"{reading.name}，{reading.level}级" if reading else "对象未识别"
    event.payload["message"] += f"，对象：{detail}"
