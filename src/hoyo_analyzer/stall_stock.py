"""Count the checked item slots on the first page of a selling stall."""

from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np

from .panels import PanelObservation
from .paths import resolve_application_path


class StallStockDetector:
    def __init__(self, template_directory: Path | None = None) -> None:
        directory = template_directory or resolve_application_path("assets/templates")
        self.items_tab = self._load(directory / "stall_items_tab.png")
        self.first_page = self._load(directory / "stall_page_one.png")

    @staticmethod
    def _load(path: Path) -> np.ndarray | None:
        return cv2.imdecode(np.fromfile(path, dtype=np.uint8), cv2.IMREAD_COLOR) if path.is_file() else None

    @staticmethod
    def _patch(image: np.ndarray, bbox: tuple[int, int, int, int], scale: float,
               offset_x: int, offset_y: int, width: int, height: int) -> np.ndarray | None:
        x = round(bbox[0] + offset_x * scale)
        y = round(bbox[1] + offset_y * scale)
        right = round(bbox[0] + (offset_x + width) * scale)
        bottom = round(bbox[1] + (offset_y + height) * scale)
        if x < 0 or y < 0 or right > image.shape[1] or bottom > image.shape[0]:
            return None
        return image[y:bottom, x:right]

    def _matches(self, image: np.ndarray, bbox: tuple[int, int, int, int], scale: float,
                 template: np.ndarray | None, offset_x: int, offset_y: int) -> bool:
        if template is None:
            return False
        height, width = template.shape[:2]
        patch = self._patch(image, bbox, scale, offset_x, offset_y, width, height)
        if patch is None:
            return False
        reference = cv2.resize(template, (patch.shape[1], patch.shape[0]), interpolation=cv2.INTER_AREA)
        error = float(np.abs(patch.astype(np.int16) - reference.astype(np.int16)).mean())
        correlation = float(cv2.matchTemplate(cv2.cvtColor(patch, cv2.COLOR_BGR2GRAY),
                                              cv2.cvtColor(reference, cv2.COLOR_BGR2GRAY),
                                              cv2.TM_CCOEFF_NORMED)[0, 0])
        return error <= 28 and correlation >= 0.9

    def count(self, image: np.ndarray, panel: PanelObservation) -> int | None:
        if panel.panel_type != "stall" or panel.bbox is None or panel.confidence < 0.9:
            return None
        scale = (panel.bbox[2] - panel.bbox[0]) / 68.0
        if not self._matches(image, panel.bbox, scale, self.items_tab, 40, 31):
            return None
        if not self._matches(image, panel.bbox, scale, self.first_page, 298, 78):
            return None
        count = 0
        for row in range(4):
            for column in range(5):
                patch = self._patch(image, panel.bbox, scale,
                                    70 + column * 51, 61 + row * 51, 22, 22)
                if patch is None:
                    return None
                hsv = cv2.cvtColor(patch, cv2.COLOR_BGR2HSV)
                green = ((hsv[:, :, 0] >= 35) & (hsv[:, :, 0] <= 85)
                         & (hsv[:, :, 1] >= 90) & (hsv[:, :, 2] >= 80))
                area_scale = scale * scale
                if (int(green.sum()) >= 75 * area_scale
                        and int(green[:max(1, round(3 * scale))].sum()) >= 20 * scale
                        and int(green[:, -max(1, round(3 * scale)):].sum()) >= 20 * scale):
                    count += 1
        return count


class StallStockTracker:
    def __init__(self, confirm_frames: int = 3, confirm_seconds: float = 5.0) -> None:
        self.confirm_frames = confirm_frames
        self.confirm_ms = round(confirm_seconds * 1000)
        self.reset()

    def reset(self) -> None:
        self.pending: int | None = None
        self.frames = 0
        self.pending_since_ms: int | None = None
        self.last_timestamp_ms: int | None = None

    def update(self, count: int | None, timestamp_ms: int) -> int | None:
        if count is None:
            self.reset()
            return None
        if (count != self.pending or self.last_timestamp_ms is None
                or timestamp_ms <= self.last_timestamp_ms
                or timestamp_ms - self.last_timestamp_ms > 5000):
            self.pending, self.frames = count, 1
            self.pending_since_ms = timestamp_ms
        else:
            self.frames += 1
        self.last_timestamp_ms = timestamp_ms
        return (count if self.frames >= self.confirm_frames and self.pending_since_ms is not None
                and timestamp_ms - self.pending_since_ms >= self.confirm_ms else None)
