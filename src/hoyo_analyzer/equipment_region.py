"""YOLO region detection for full Dream 2 screenshots.

This module deliberately contains only the model adapter.  It does not know
anything about OCR or UI parsing: its job is to find the one
``equipment_tooltip`` rectangle in a full game screenshot so a downstream OCR
engine can read a much smaller, cleaner image.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

BBox = tuple[int, int, int, int]


@dataclass(frozen=True, slots=True)
class EquipmentRegionDetection:
    """The best equipment tooltip detection from one screenshot."""

    bbox: BBox
    confidence: float
    class_id: int = 0
    class_name: str = "equipment_tooltip"


class YoloEquipmentRegionDetector:
    """Lazy Ultralytics YOLO adapter for one-shot screenshot recognition.

    The model is loaded only when :meth:`detect` is first called.  That keeps
    the desktop UI responsive and allows the application to start even before
    the user has trained/copied ``best.pt`` into the models directory.
    """

    REQUIRED_CLASS_NAME = "equipment_tooltip"

    def __init__(
        self,
        model_path: str | Path,
        confidence: float = 0.35,
        image_size: int = 640,
        device: str = "auto",
    ) -> None:
        self.model_path = Path(model_path)
        self.confidence = float(confidence)
        self.image_size = int(image_size)
        self.device = str(device)
        self._model: Any | None = None
        self._load_attempted = False
        self.last_error: str | None = None

    def _resolve_device(self) -> str | int | None:
        if self.device.casefold() in {"", "auto"}:
            return None
        if self.device.isdigit():
            return int(self.device)
        return self.device

    def _ensure_model(self) -> bool:
        if self._model is not None:
            return True
        if self._load_attempted:
            return False
        self._load_attempted = True
        if not self.model_path.is_file():
            self.last_error = f"YOLO模型文件不存在：{self.model_path}"
            return False
        try:
            from ultralytics import YOLO  # type: ignore[import-not-found]

            self._model = YOLO(str(self.model_path))
            return True
        except Exception as exc:  # noqa: BLE001 - optional model must not crash the UI
            self.last_error = f"YOLO模型加载失败：{type(exc).__name__}: {exc}"
            return False

    @staticmethod
    def _class_name(result: Any, class_id: int) -> str:
        names = getattr(result, "names", None)
        if isinstance(names, dict):
            return str(names.get(class_id, f"class_{class_id}"))
        if isinstance(names, (list, tuple)) and 0 <= class_id < len(names):
            return str(names[class_id])
        return f"class_{class_id}"

    def detect(self, image: np.ndarray) -> EquipmentRegionDetection | None:
        """Return the highest-confidence equipment tooltip rectangle."""
        if not isinstance(image, np.ndarray) or image.size == 0:
            self.last_error = "输入图片为空"
            return None
        if not self._ensure_model():
            return None

        kwargs: dict[str, Any] = {
            "source": image,
            "conf": self.confidence,
            "imgsz": self.image_size,
            "verbose": False,
        }
        device = self._resolve_device()
        if device is not None:
            kwargs["device"] = device
        try:
            results = self._model.predict(**kwargs)
        except Exception as exc:  # noqa: BLE001 - keep the dialog usable
            self.last_error = f"YOLO推理失败：{type(exc).__name__}: {exc}"
            return None
        if not results:
            return None

        result = results[0]
        boxes = getattr(result, "boxes", None)
        if boxes is None:
            return None
        try:
            count = len(boxes)
        except TypeError:
            return None

        height, width = image.shape[:2]
        best: EquipmentRegionDetection | None = None
        for index in range(count):
            try:
                xyxy = np.asarray(boxes.xyxy[index].tolist(), dtype=float).reshape(-1)
                score = float(np.asarray(boxes.conf[index].tolist()).reshape(-1)[0])
                class_id = int(np.asarray(boxes.cls[index].tolist()).reshape(-1)[0]) if hasattr(boxes, "cls") else 0
            except (AttributeError, IndexError, TypeError, ValueError):
                continue
            if xyxy.size < 4:
                continue
            class_name = self._class_name(result, class_id)
            # A class-id fallback makes models exported without names usable,
            # while named classes remain the safe/default path.
            if class_name != self.REQUIRED_CLASS_NAME and not (
                class_name.startswith("class_") and class_id == 0
            ):
                continue
            bbox: BBox = (
                max(0, min(width, round(float(xyxy[0])))),
                max(0, min(height, round(float(xyxy[1])))),
                max(0, min(width, round(float(xyxy[2])))),
                max(0, min(height, round(float(xyxy[3])))),
            )
            if bbox[2] <= bbox[0] or bbox[3] <= bbox[1]:
                continue
            candidate = EquipmentRegionDetection(bbox, score, class_id, class_name)
            if best is None or candidate.confidence > best.confidence:
                best = candidate
        return best

    def __call__(self, image: np.ndarray) -> EquipmentRegionDetection | None:
        return self.detect(image)
