"""Safe, configuration-friendly region-of-interest helpers."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True, slots=True)
class PixelROI:
    x: int
    y: int
    width: int
    height: int

    def clamp(self, image_width: int, image_height: int) -> PixelROI:
        x = min(max(0, self.x), image_width)
        y = min(max(0, self.y), image_height)
        right = min(max(x, self.x + max(0, self.width)), image_width)
        bottom = min(max(y, self.y + max(0, self.height)), image_height)
        return PixelROI(x, y, right - x, bottom - y)


@dataclass(frozen=True, slots=True)
class RelativeROI:
    x: float
    y: float
    width: float
    height: float

    def to_pixels(self, image_width: int, image_height: int) -> PixelROI:
        return PixelROI(
            round(self.x * image_width),
            round(self.y * image_height),
            round(self.width * image_width),
            round(self.height * image_height),
        ).clamp(image_width, image_height)


def crop_roi(image: np.ndarray, roi: PixelROI | RelativeROI) -> np.ndarray:
    if image.ndim < 2:
        raise ValueError("image 至少需要有二维高宽")
    h, w = image.shape[:2]
    r = roi.to_pixels(w, h) if isinstance(roi, RelativeROI) else roi.clamp(w, h)
    return image[r.y : r.y + r.height, r.x : r.x + r.width].copy()
