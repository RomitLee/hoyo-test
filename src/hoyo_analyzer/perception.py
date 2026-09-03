"""Lightweight perception primitives used before YOLO/OCR are integrated."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

import numpy as np

from .models import FramePacket, Observation
from .ocr import NullOcrEngine, OcrEngine
from .roi import PixelROI, RelativeROI, crop_roi


class PerceptionEngine(Protocol):
    def observe(self, packet: FramePacket) -> Observation: ...


@dataclass(slots=True)
class TemplateSpec:
    signal: str
    path: str
    threshold: float = 0.82
    roi: PixelROI | RelativeROI | None = None


class RuleBasedPerception:
    """Baseline frame-change detector plus optional OpenCV template matching.

    The semantic signals expected by EventMachine can later be supplied by YOLO/OCR
    adapters. This class intentionally does not claim that raw pixel changes alone
    identify a map or an item.
    """

    def __init__(
        self, templates: list[TemplateSpec] | None = None, ocr: OcrEngine | None = None, change_threshold: float = 0.025
    ) -> None:
        self.templates = templates or []
        self.ocr = ocr or NullOcrEngine()
        self.change_threshold = change_threshold
        self._previous_gray: np.ndarray | None = None
        self._loaded_templates: dict[str, np.ndarray] = {}

    def _gray(self, image: np.ndarray) -> np.ndarray:
        if image.ndim == 2:
            return image
        try:
            import cv2

            return cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        except ImportError:
            return image.mean(axis=2).astype(np.uint8)

    def _template_signals(self, image: np.ndarray) -> dict[str, Any]:
        if not self.templates:
            return {}
        try:
            import cv2
        except ImportError:
            return {}
        signals: dict[str, Any] = {}
        for spec in self.templates:
            template = self._loaded_templates.get(spec.path)
            if template is None:
                template = cv2.imread(str(Path(spec.path)), cv2.IMREAD_GRAYSCALE)
                if template is None:
                    continue
                self._loaded_templates[spec.path] = template
            search = crop_roi(image, spec.roi) if spec.roi is not None else image
            search_gray = self._gray(search)
            if search_gray.shape[0] < template.shape[0] or search_gray.shape[1] < template.shape[1]:
                continue
            result = cv2.matchTemplate(search_gray, template, cv2.TM_CCOEFF_NORMED)
            _, score, _, _ = cv2.minMaxLoc(result)
            signals[spec.signal] = {"active": bool(score >= spec.threshold), "score": float(score)}
        return signals

    def observe(self, packet: FramePacket) -> Observation:
        gray = self._gray(packet.image)
        changed = False
        change_score = 0.0
        if self._previous_gray is not None and self._previous_gray.shape == gray.shape:
            change_score = float(
                np.abs(gray.astype(np.float32) - self._previous_gray.astype(np.float32)).mean() / 255.0
            )
            changed = change_score >= self.change_threshold
        self._previous_gray = gray
        signals: dict[str, Any] = {"frame_changed": changed, "change_score": change_score}
        signals.update(self._template_signals(packet.image))
        return Observation(
            packet.frame_index, packet.timestamp_ms, signals, min(1.0, 0.5 + change_score), "rules+templates"
        )
