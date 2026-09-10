"""YOLO-based detectors for Dream 2 UI panels.

The production model is a two-class detector:

* ``equipment_tooltip`` - equipment attribute tooltip;
* ``inventory_panel`` - the open inventory panel.

The model is executed once per frame. The detector keeps independent IoU/
stability tracking for each class and exposes the equipment class at the top
level for backwards compatibility, while returning every detected class under
``detections``.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

import numpy as np

BBox = tuple[int, int, int, int]
ModelLoader = Callable[[str], Any]


class YOLOTooltipDetector:
    """Detect and stabilize the UI classes used by the perception pipeline."""

    PRIMARY_CLASS_NAME = "equipment_tooltip"
    INVENTORY_CLASS_NAME = "inventory_panel"
    CLASS_NAMES = (PRIMARY_CLASS_NAME, INVENTORY_CLASS_NAME)

    def __init__(
        self,
        model_path: str | Path,
        confidence: float = 0.65,
        image_size: int = 640,
        device: str = "auto",
        stable_frames: int = 3,
        model_loader: ModelLoader | None = None,
        stability_iou: float = 0.55,
    ) -> None:
        self.model_path = Path(model_path)
        self.confidence = float(confidence)
        self.image_size = int(image_size)
        self.device = str(device)
        self.stable_frames_required = max(1, int(stable_frames))
        self.stability_iou = float(stability_iou)
        self._model_loader = model_loader
        self._model: Any | None = None
        self._load_attempted = False
        self._load_error: str | None = None
        self._last_bboxes: dict[str, BBox | None] = {name: None for name in self.CLASS_NAMES}
        self._stable_frames_by_class: dict[str, int] = {name: 0 for name in self.CLASS_NAMES}

    @staticmethod
    def _iou(first: BBox, second: BBox) -> float:
        left = max(first[0], second[0])
        top = max(first[1], second[1])
        right = min(first[2], second[2])
        bottom = min(first[3], second[3])
        intersection = max(0, right - left) * max(0, bottom - top)
        first_area = max(0, first[2] - first[0]) * max(0, first[3] - first[1])
        second_area = max(0, second[2] - second[0]) * max(0, second[3] - second[1])
        union = first_area + second_area - intersection
        return intersection / union if union else 0.0

    def _reset_class(self, class_name: str) -> None:
        self._last_bboxes[class_name] = None
        self._stable_frames_by_class[class_name] = 0

    def _base_signal(self, *, reason: str | None = None) -> dict[str, Any]:
        signal: dict[str, Any] = {
            "active": False,
            "candidate": False,
            "stable_frames": self._stable_frames_by_class[self.PRIMARY_CLASS_NAME],
            "model_path": str(self.model_path),
            "detections": {},
        }
        if reason:
            signal["reason"] = reason
        if self._load_error:
            signal["error"] = self._load_error
        return signal

    def _ensure_model(self) -> bool:
        if self._model is not None:
            return True
        if self._load_attempted:
            return False
        self._load_attempted = True

        if not self.model_path.is_file():
            self._load_error = f"YOLO模型文件不存在：{self.model_path}"
            return False

        try:
            loader = self._model_loader
            if loader is None:
                from ultralytics import YOLO  # type: ignore[import-not-found]

                loader = YOLO
            self._model = loader(str(self.model_path))
            return True
        except Exception as exc:  # noqa: BLE001 - optional dependency/model errors become data
            self._load_error = f"YOLO模型加载失败：{type(exc).__name__}: {exc}"
            self._model = None
            return False

    def _resolve_device(self) -> str | int | None:
        if self.device.casefold() in {"", "auto"}:
            return None
        if self.device.isdigit():
            return int(self.device)
        return self.device

    @staticmethod
    def _class_name(result: Any, class_id: int) -> str:
        names = getattr(result, "names", None)
        if isinstance(names, dict):
            return str(names.get(class_id, f"class_{class_id}"))
        if isinstance(names, (list, tuple)) and 0 <= class_id < len(names):
            return str(names[class_id])
        return f"class_{class_id}"

    def _predict(self, image: np.ndarray) -> Any | None:
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
            return results[0] if results else None
        except Exception as exc:  # noqa: BLE001 - keep live analysis alive and surface a useful reason
            self._load_error = f"YOLO推理失败：{type(exc).__name__}: {exc}"
            return None

    def _detections_by_class(self, result: Any) -> dict[str, tuple[BBox, float, int, str]]:
        if result is None:
            return {}
        boxes = getattr(result, "boxes", None)
        if boxes is None:
            return {}
        try:
            count = len(boxes)
        except TypeError:
            return {}
        detections: dict[str, tuple[BBox, float, int, str]] = {}
        for index in range(count):
            try:
                xyxy = np.asarray(boxes.xyxy[index].tolist(), dtype=float).reshape(-1)
                score = float(np.asarray(boxes.conf[index].tolist()).reshape(-1)[0])
                class_id = int(np.asarray(boxes.cls[index].tolist()).reshape(-1)[0]) if hasattr(boxes, "cls") else 0
            except (AttributeError, IndexError, TypeError, ValueError):
                continue
            if xyxy.size < 4:
                continue
            bbox = tuple(round(float(value)) for value in xyxy[:4])
            bbox = (max(0, bbox[0]), max(0, bbox[1]), max(0, bbox[2]), max(0, bbox[3]))
            if bbox[2] <= bbox[0] or bbox[3] <= bbox[1]:
                continue
            class_name = self._class_name(result, class_id)
            if class_name not in self.CLASS_NAMES:
                continue
            candidate = (bbox, score, class_id, class_name)
            current = detections.get(class_name)
            if current is None or score > current[1]:
                detections[class_name] = candidate
        return detections

    def _stable_signal(
        self,
        class_name: str,
        detection: tuple[BBox, float, int, str] | None,
    ) -> dict[str, Any]:
        if detection is None:
            self._reset_class(class_name)
            return {
                "active": False,
                "candidate": False,
                "stable_frames": 0,
                "class_name": class_name,
            }
        bbox, score, class_id, returned_name = detection
        last_bbox = self._last_bboxes[class_name]
        if last_bbox is not None and self._iou(last_bbox, bbox) >= self.stability_iou:
            self._stable_frames_by_class[class_name] += 1
        else:
            self._stable_frames_by_class[class_name] = 1
        self._last_bboxes[class_name] = bbox
        return {
            "active": self._stable_frames_by_class[class_name] >= self.stable_frames_required,
            "candidate": True,
            "bbox": list(bbox),
            "score": round(score, 4),
            "class_id": class_id,
            "class_name": returned_name,
            "stable_frames": self._stable_frames_by_class[class_name],
            "model_path": str(self.model_path),
        }

    def update(self, image: np.ndarray) -> dict[str, Any]:
        """Run one inference and return equipment plus all supported class signals."""
        if not isinstance(image, np.ndarray) or image.size == 0:
            for class_name in self.CLASS_NAMES:
                self._reset_class(class_name)
            return self._base_signal(reason="invalid_image")

        result = self._predict(image)
        detections = self._detections_by_class(result)
        class_signals = {
            class_name: self._stable_signal(class_name, detections.get(class_name)) for class_name in self.CLASS_NAMES
        }
        primary = dict(class_signals[self.PRIMARY_CLASS_NAME])
        primary["model_path"] = str(self.model_path)
        primary["detections"] = class_signals
        if result is None and not self._model:
            reason = "no_detection"
            if self._load_error and "不存在" in self._load_error:
                reason = "yolo_model_not_found"
            elif self._load_error and "加载失败" in self._load_error:
                reason = "yolo_model_load_failed"
            elif self._load_error and "推理失败" in self._load_error:
                reason = "yolo_inference_failed"
            primary["reason"] = reason
            if self._load_error:
                primary["error"] = self._load_error
        return primary
