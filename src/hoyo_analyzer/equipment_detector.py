"""OpenCV rule-based detector for dark equipment-property tooltip panels."""

from __future__ import annotations

from dataclasses import dataclass
from math import log
from typing import Any

import cv2
import numpy as np

BBox = tuple[int, int, int, int]


@dataclass(frozen=True, slots=True)
class TooltipDetection:
    """One scored rectangular tooltip candidate in source-frame coordinates."""

    bbox: BBox
    score: float
    dark_ratio: float
    bright_ratio: float
    edge_density: float
    text_lines: int
    title_ratio: float
    yellow_ratio: float
    color_ratio: float
    border_score: float
    supported_border_sides: int

    @property
    def width(self) -> int:
        return self.bbox[2] - self.bbox[0]

    @property
    def height(self) -> int:
        return self.bbox[3] - self.bbox[1]

    def to_signal(self, *, active: bool, stable_frames: int) -> dict[str, Any]:
        return {
            "active": active,
            "candidate": True,
            "bbox": list(self.bbox),
            "score": round(self.score, 4),
            "stable_frames": stable_frames,
            "width": self.width,
            "height": self.height,
            "dark_ratio": round(self.dark_ratio, 4),
            "bright_ratio": round(self.bright_ratio, 4),
            "edge_density": round(self.edge_density, 4),
            "text_lines": self.text_lines,
            "title_ratio": round(self.title_ratio, 4),
            "yellow_ratio": round(self.yellow_ratio, 4),
            "color_ratio": round(self.color_ratio, 4),
            "border_score": round(self.border_score, 4),
            "supported_border_sides": self.supported_border_sides,
        }


@dataclass(slots=True)
class TooltipDetectorConfig:
    """Resolution-independent constraints for a tooltip-like panel."""

    min_score: float = 0.88
    stable_frames: int = 3
    require_title: bool = True
    min_title_ratio: float = 0.075
    min_dark_ratio: float = 0.72
    max_bright_ratio: float = 0.16
    max_color_ratio: float = 0.25
    min_border_score: float = 0.75
    min_text_lines: int = 3
    allow_generic_fallback: bool = False
    min_width_ratio: float = 0.12
    max_width_ratio: float = 0.72
    min_height_ratio: float = 0.10
    max_height_ratio: float = 0.88
    min_area_ratio: float = 0.015
    max_area_ratio: float = 0.48
    stability_iou: float = 0.55


class OpenCVTooltipDetector:
    """Locate stable dark rectangular panels containing multiple bright text rows.

    The detector intentionally uses only explainable OpenCV features so collected
    crops can later be reviewed and converted into a YOLO training dataset.
    """

    def __init__(self, config: TooltipDetectorConfig | None = None) -> None:
        self.config = config or TooltipDetectorConfig()
        self._last_bbox: BBox | None = None
        self._stable_frames = 0

    @staticmethod
    def _clamp01(value: float) -> float:
        return max(0.0, min(1.0, value))

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

    def _valid_geometry(self, bbox: BBox, frame_width: int, frame_height: int) -> bool:
        x1, y1, x2, y2 = bbox
        width, height = x2 - x1, y2 - y1
        area_ratio = (width * height) / float(frame_width * frame_height)
        return (
            self.config.min_width_ratio <= width / frame_width <= self.config.max_width_ratio
            and self.config.min_height_ratio <= height / frame_height <= self.config.max_height_ratio
            and self.config.min_area_ratio <= area_ratio <= self.config.max_area_ratio
            and 0.35 <= width / max(1, height) <= 4.5
        )

    @staticmethod
    def _line_candidate_boxes(gray: np.ndarray) -> list[BBox]:
        """Build rectangles from long border-like horizontal and vertical lines.

        Dream 2 tooltips are translucent: their background is not always a
        connected dark contour, especially when the panel reaches the bottom
        edge of the game window. The screenshot still normally contains a long
        top edge and two side edges, so a lightweight Hough pass recovers this
        common layout without requiring a model.
        """
        frame_height, frame_width = gray.shape
        edges = cv2.Canny(cv2.GaussianBlur(gray, (3, 3), 0), 20, 80)
        lines = cv2.HoughLinesP(
            edges,
            1,
            np.pi / 180,
            threshold=max(35, round(min(frame_width, frame_height) * 0.07)),
            minLineLength=max(70, round(min(frame_width, frame_height) * 0.14)),
            maxLineGap=max(8, round(min(frame_width, frame_height) * 0.025)),
        )
        if lines is None:
            return []

        horizontal: list[tuple[int, int, int]] = []
        vertical: list[tuple[int, int, int]] = []
        horizontal_tolerance = max(3, round(frame_height * 0.012))
        vertical_tolerance = max(3, round(frame_width * 0.012))
        min_horizontal_length = max(70, round(frame_width * 0.14))
        min_vertical_length = max(70, round(frame_height * 0.14))
        for line in lines:
            x1, y1, x2, y2 = (int(value) for value in line[0])
            dx, dy = abs(x2 - x1), abs(y2 - y1)
            if dx >= min_horizontal_length and dy <= horizontal_tolerance:
                horizontal.append((min(x1, x2), max(x1, x2), round((y1 + y2) / 2)))
            elif dy >= min_vertical_length and dx <= vertical_tolerance:
                vertical.append((round((x1 + x2) / 2), min(y1, y2), max(y1, y2)))

        # HoughLinesP commonly returns many overlapping fragments for one
        # border. Merge nearby lines before pairing them; otherwise the naive
        # vertical x vertical x horizontal combination grows quadratically and
        # makes the live detector needlessly expensive.
        def merge_horizontal(items: list[tuple[int, int, int]]) -> list[tuple[int, int, int]]:
            merged: list[list[int]] = []
            for start, end, coordinate in sorted(items, key=lambda item: item[2]):
                if merged and coordinate - merged[-1][2] <= horizontal_tolerance:
                    merged[-1][0] = min(merged[-1][0], start)
                    merged[-1][1] = max(merged[-1][1], end)
                    merged[-1][2] = round((merged[-1][2] + coordinate) / 2)
                else:
                    merged.append([start, end, coordinate])
            return [tuple(item) for item in merged]

        def merge_vertical(items: list[tuple[int, int, int]]) -> list[tuple[int, int, int]]:
            merged: list[list[int]] = []
            for coordinate, start, end in sorted(items, key=lambda item: item[0]):
                if merged and coordinate - merged[-1][0] <= vertical_tolerance:
                    merged[-1][1] = min(merged[-1][1], start)
                    merged[-1][2] = max(merged[-1][2], end)
                    merged[-1][0] = round((merged[-1][0] + coordinate) / 2)
                else:
                    merged.append([coordinate, start, end])
            return [tuple(item) for item in merged]

        horizontal = merge_horizontal(horizontal)
        vertical = merge_vertical(vertical)
        # Keep the candidate-generation stage bounded even on noisy game
        # scenes. Longer lines are more likely to be tooltip borders.
        horizontal = sorted(horizontal, key=lambda item: item[1] - item[0], reverse=True)[:48]
        vertical = sorted(vertical, key=lambda item: item[2] - item[1], reverse=True)[:32]

        boxes: list[BBox] = []
        for left_x, left_top, left_bottom in vertical:
            for right_x, right_top, right_bottom in vertical:
                if right_x - left_x < frame_width * 0.15:
                    continue
                for line_left, line_right, top_y in horizontal:
                    overlap_left = max(left_x, line_left)
                    overlap_right = min(right_x, line_right)
                    if overlap_right - overlap_left < (right_x - left_x) * 0.55:
                        continue
                    top_tolerance = max(horizontal_tolerance * 2, round(frame_height * 0.035))
                    if not (left_top - top_tolerance <= top_y <= left_top + top_tolerance):
                        continue
                    if not (right_top - top_tolerance <= top_y <= right_top + top_tolerance):
                        continue
                    bottom_y = min(frame_height, max(left_bottom, right_bottom) + 1)
                    x1 = max(0, left_x - 1)
                    x2 = min(frame_width, right_x + 1)
                    y1 = max(0, top_y - 1)
                    if bottom_y - y1 >= frame_height * 0.12:
                        boxes.append((x1, y1, x2, bottom_y))
        return boxes

    @staticmethod
    def _bottom_edge_candidate_boxes(gray: np.ndarray) -> list[BBox]:
        """Find dark panels whose lower border is clipped by the frame edge.

        A tooltip can be rendered below the cursor and continue past the game
        window's bottom edge. In that case no closed contour exists. We look
        for a strong dark-density jump at a possible top border and keep a
        small number of likely width/position combinations for normal scoring.
        The integral images keep this fallback inexpensive enough for live use.
        """
        frame_height, frame_width = gray.shape
        scale = 2
        small_width = max(1, frame_width // scale)
        small_height = max(1, frame_height // scale)
        small = cv2.resize(gray, (small_width, small_height), interpolation=cv2.INTER_AREA)
        dark = (small < 125).astype(np.float32)
        integral = cv2.integral(dark)

        def area_mean(x: int, y: int, width: int, height: int) -> float:
            return float(
                (integral[y + height, x + width] - integral[y, x + width] - integral[y + height, x] + integral[y, x])
                / max(1, width * height)
            )

        candidates: list[BBox] = []
        width_ratios = (0.28, 0.34, 0.36, 0.37, 0.38, 0.40, 0.46, 0.52)
        x_step = max(4, round(12 / scale))
        y_step = max(3, round(8 / scale))
        band_height = max(4, round(24 / scale))
        min_top = round(small_height * 0.18)
        max_top = small_height - round(small_height * 0.10)
        for width_ratio in width_ratios:
            candidate_width = max(1, round(small_width * width_ratio))
            best: list[tuple[float, int, int]] = []
            for x in range(0, small_width - candidate_width + 1, x_step):
                for top in range(min_top, max_top + 1, y_step):
                    height = small_height - top
                    if height <= 0:
                        continue
                    inside = area_mean(x, top, candidate_width, height)
                    above_y = max(0, top - band_height)
                    above_height = min(band_height, top - above_y)
                    if above_height <= 0:
                        continue
                    above = area_mean(x, above_y, candidate_width, above_height)
                    transition = inside - above
                    if inside < 0.55 or transition < 0.16:
                        continue
                    best.append((transition + inside * 0.35, x, top))
            for _, x, top in sorted(best, reverse=True)[:3]:
                candidates.append(
                    (
                        x * scale,
                        top * scale,
                        min(frame_width, (x + candidate_width) * scale),
                        frame_height,
                    )
                )
        return candidates

    @staticmethod
    def _title_candidate_boxes(image: np.ndarray) -> list[BBox]:
        """Infer tooltip rectangles from the yellow equipment-name row.

        In the current game skin, an equipment tooltip has a very stable layout:
        the yellow equipment name starts roughly 38% into the panel and about
        8-14 pixels below its top border at a 999x749 capture.  Using that
        coloured title cue is much more selective than treating every large dark
        area (chat panel, inventory footer, etc.) as a tooltip.
        """
        if image.ndim != 3:
            return []
        frame_height, frame_width = image.shape[:2]
        hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
        hue, saturation, value = cv2.split(hsv)
        yellow = ((hue >= 18) & (hue <= 40) & (saturation >= 110) & (value >= 150)).astype(np.uint8) * 255

        # Join the separated strokes/characters of one Chinese title row.
        horizontal_kernel = max(7, round(frame_width * 0.013))
        yellow = cv2.morphologyEx(
            yellow,
            cv2.MORPH_CLOSE,
            np.ones((3, horizontal_kernel), np.uint8),
            iterations=1,
        )
        yellow = cv2.dilate(yellow, np.ones((2, 3), np.uint8), iterations=1)
        contours, _ = cv2.findContours(yellow, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

        boxes: list[BBox] = []
        min_title_width = frame_width * 0.045
        max_title_width = frame_width * 0.17
        min_title_height = frame_height * 0.018
        max_title_height = frame_height * 0.045
        for contour in contours:
            title_x, title_y, title_width, title_height = cv2.boundingRect(contour)
            if not (
                min_title_width <= title_width <= max_title_width
                and min_title_height <= title_height <= max_title_height
                and frame_height * 0.18 <= title_y <= frame_height * 0.82
            ):
                continue

            for width_ratio in (0.392,):
                panel_width = round(frame_width * width_ratio)
                panel_x = round(title_x - panel_width * 0.38)
                panel_x = max(0, min(frame_width - panel_width, panel_x))
                for top_offset_ratio in (0.011, 0.015, 0.019):
                    panel_y = max(0, title_y - round(frame_height * top_offset_ratio))
                    for height_ratio in (0.28, 0.32, 0.35, 0.36):
                        panel_bottom = min(frame_height, panel_y + round(frame_height * height_ratio))
                        boxes.append((panel_x, panel_y, panel_x + panel_width, panel_bottom))
        return boxes

    @staticmethod
    def _border_support(edges: np.ndarray, bbox: BBox) -> tuple[float, tuple[float, float, float, float]]:
        """Measure how strongly Canny edges support the four candidate borders."""
        frame_height, frame_width = edges.shape
        x1, y1, x2, y2 = bbox
        radius = max(3, round(min(frame_height, frame_width) * 0.005))

        def horizontal(y: int) -> float:
            start, stop = max(0, y - radius), min(frame_height, y + radius + 1)
            return max((float(np.mean(edges[row, x1:x2] > 0)) for row in range(start, stop)), default=0.0)

        def vertical(x: int) -> float:
            start, stop = max(0, x - radius), min(frame_width, x + radius + 1)
            return max((float(np.mean(edges[y1:y2, column] > 0)) for column in range(start, stop)), default=0.0)

        top = horizontal(y1)
        bottom = 1.0 if y2 >= frame_height else horizontal(y2 - 1)
        left = 1.0 if x1 <= 0 else vertical(x1)
        right = 1.0 if x2 >= frame_width else vertical(x2 - 1)
        sides = (top, bottom, left, right)
        return sum(sides) / len(sides), sides

    @staticmethod
    def _candidate_boxes(gray: np.ndarray) -> list[BBox]:
        height, width = gray.shape
        blurred = cv2.GaussianBlur(gray, (5, 5), 0)
        edges = cv2.Canny(blurred, 35, 110)
        edges = cv2.morphologyEx(edges, cv2.MORPH_CLOSE, np.ones((5, 5), np.uint8), iterations=2)

        # Property panels are normally dark but contain bright coloured text. A
        # second mask catches panels whose border is too subtle for Canny alone.
        dark = cv2.inRange(blurred, 0, 125)
        dark = cv2.morphologyEx(dark, cv2.MORPH_CLOSE, np.ones((9, 9), np.uint8), iterations=2)
        combined = cv2.bitwise_or(edges, dark)

        boxes: list[BBox] = []
        for mask in (edges, dark, combined):
            contours, _ = cv2.findContours(mask, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
            for contour in contours:
                x, y, candidate_width, candidate_height = cv2.boundingRect(contour)
                if candidate_width < 30 or candidate_height < 30:
                    continue
                padding = max(2, round(min(candidate_width, candidate_height) * 0.01))
                boxes.append(
                    (
                        max(0, x - padding),
                        max(0, y - padding),
                        min(width, x + candidate_width + padding),
                        min(height, y + candidate_height + padding),
                    )
                )
        boxes.extend(OpenCVTooltipDetector._line_candidate_boxes(gray))
        bottom_candidates = OpenCVTooltipDetector._bottom_edge_candidate_boxes(gray)
        boxes.extend(bottom_candidates)

        # The left side of a translucent tooltip may be the only part that
        # survives the dark-mask contour pass (the item icon is especially
        # helpful here). Reuse that contour as an anchor and extend it to the
        # right using the width suggested by the bottom-edge candidates.
        bottom_anchors = [
            candidate
            for candidate in boxes
            if candidate[3] >= height - max(4, round(height * 0.05))
            and candidate[2] - candidate[0] >= width * 0.10
            and candidate[1] >= height * 0.45
        ]
        for candidate in bottom_candidates:
            candidate_width = candidate[2] - candidate[0]
            for anchor in bottom_anchors:
                gap = candidate[0] - anchor[2]
                if 0 <= gap <= width * 0.08 and anchor[0] < candidate[0]:
                    boxes.append(
                        (
                            anchor[0],
                            candidate[1],
                            min(width, anchor[0] + candidate_width),
                            height,
                        )
                    )
        return boxes

    @staticmethod
    def _count_text_lines(gray_patch: np.ndarray) -> int:
        bright = cv2.inRange(gray_patch, 145, 255)
        kernel_width = max(5, round(gray_patch.shape[1] * 0.035))
        merged = cv2.morphologyEx(
            bright,
            cv2.MORPH_CLOSE,
            np.ones((1, kernel_width), np.uint8),
            iterations=1,
        )
        contours, _ = cv2.findContours(merged, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        rows: list[tuple[int, int]] = []
        for contour in contours:
            _, y, width, height = cv2.boundingRect(contour)
            if width < gray_patch.shape[1] * 0.06 or height > gray_patch.shape[0] * 0.18:
                continue
            rows.append((y, y + height))
        if not rows:
            return 0
        rows.sort()
        merged_rows = [rows[0]]
        tolerance = max(2, round(gray_patch.shape[0] * 0.015))
        for start, end in rows[1:]:
            previous_start, previous_end = merged_rows[-1]
            if start <= previous_end + tolerance:
                merged_rows[-1] = (previous_start, max(previous_end, end))
            else:
                merged_rows.append((start, end))
        return len(merged_rows)

    def _score(
        self,
        gray: np.ndarray,
        bbox: BBox,
        *,
        edges: np.ndarray | None = None,
        yellow_mask: np.ndarray | None = None,
        color_mask: np.ndarray | None = None,
    ) -> TooltipDetection | None:
        x1, y1, x2, y2 = bbox
        patch = gray[y1:y2, x1:x2]
        if patch.size == 0:
            return None

        if edges is None:
            edges = cv2.Canny(cv2.GaussianBlur(gray, (3, 3), 0), 20, 80)
        local_edges = edges[y1:y2, x1:x2]
        dark_ratio = float(np.count_nonzero(patch < 115) / patch.size)
        bright_ratio = float(np.count_nonzero(patch > 145) / patch.size)
        edge_density = float(np.count_nonzero(local_edges) / patch.size)
        text_lines = self._count_text_lines(patch)

        width, height = x2 - x1, y2 - y1
        _, frame_width = gray.shape
        aspect = width / max(1, height)
        width_ratio = width / max(1, frame_width)
        width_score = self._clamp01(1.0 - abs(width_ratio - 0.392) / 0.10)
        aspect_score = self._clamp01(1.0 - abs(log(max(0.01, aspect) / 1.60)) / 0.80)
        shape_score = 0.65 * width_score + 0.35 * aspect_score
        dark_score = self._clamp01((dark_ratio - 0.45) / 0.35)
        edge_score = self._clamp01(edge_density / 0.18)
        text_score = self._clamp01(text_lines / 6.0)
        border_score, border_sides = self._border_support(edges, bbox)

        yellow_ratio = 0.0
        title_ratio = 0.0
        if yellow_mask is not None:
            yellow_patch = yellow_mask[y1:y2, x1:x2]
            yellow_ratio = float(np.mean(yellow_patch > 0))
            title_x1 = round(width * 0.32)
            title_x2 = max(title_x1 + 1, round(width * 0.78))
            title_y2 = max(1, round(height * 0.16))
            title_zone = yellow_patch[:title_y2, title_x1:title_x2]
            if title_zone.size:
                title_ratio = float(np.mean(title_zone > 0))

        color_ratio = 0.0
        if color_mask is not None:
            color_patch = color_mask[y1:y2, x1:x2]
            color_ratio = float(np.mean(color_patch > 0))

        title_score = self._clamp01(title_ratio / 0.08)
        yellow_score = self._clamp01(yellow_ratio / 0.03)
        color_score = self._clamp01(color_ratio / 0.10)
        score = (
            0.27 * border_score
            + 0.22 * title_score
            + 0.13 * shape_score
            + 0.10 * dark_score
            + 0.08 * color_score
            + 0.06 * yellow_score
            + 0.08 * text_score
            + 0.06 * edge_score
        )

        # A real equipment panel has a yellow name in the expected top area and
        # at least a mostly recognisable frame. These penalties specifically
        # suppress the inventory footer/chat area that previously won only
        # because it was large and dark.
        supported_sides = sum(side >= 0.35 for side in border_sides)
        if title_ratio < 0.025:
            score *= 0.42
        if border_score < 0.42 or supported_sides < 2:
            score *= 0.60
        if yellow_ratio < 0.008 or color_ratio < 0.04:
            score *= 0.70
        if text_lines < 2 or dark_ratio < 0.30:
            score *= 0.55
        return TooltipDetection(
            bbox=bbox,
            score=score,
            dark_ratio=dark_ratio,
            bright_ratio=bright_ratio,
            edge_density=edge_density,
            text_lines=text_lines,
            title_ratio=title_ratio,
            yellow_ratio=yellow_ratio,
            color_ratio=color_ratio,
            border_score=border_score,
            supported_border_sides=supported_sides,
        )

    def _passes_hard_rules(self, detection: TooltipDetection) -> bool:
        """Reject UI bars and icon grids even when their weighted score is high.

        Weighted scoring ranks plausible rectangles, while these constraints
        describe properties shared by the currently collected real equipment
        panels. Keeping both stages separate makes false positives explainable
        and lets future samples tune one threshold at a time.
        """
        config = self.config
        return (
            detection.score >= config.min_score
            and (not config.require_title or detection.title_ratio >= config.min_title_ratio)
            and detection.dark_ratio >= config.min_dark_ratio
            and detection.bright_ratio <= config.max_bright_ratio
            and detection.color_ratio <= config.max_color_ratio
            and detection.border_score >= config.min_border_score
            and detection.text_lines >= config.min_text_lines
        )

    @staticmethod
    def _deduplicate(detections: list[TooltipDetection]) -> list[TooltipDetection]:
        selected: list[TooltipDetection] = []
        for detection in sorted(detections, key=lambda item: item.score, reverse=True):
            if all(OpenCVTooltipDetector._iou(detection.bbox, item.bbox) < 0.75 for item in selected):
                selected.append(detection)
        return selected

    def detect(self, image: np.ndarray) -> TooltipDetection | None:
        """Return the highest-scoring tooltip candidate, if one passes the threshold."""
        if image.ndim not in (2, 3) or image.size == 0:
            return None
        color = image if image.ndim == 3 else cv2.cvtColor(image, cv2.COLOR_GRAY2BGR)
        gray = image if image.ndim == 2 else cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        frame_height, frame_width = gray.shape

        title_boxes = set(self._title_candidate_boxes(color))
        # A yellow title proposal is both more accurate and much cheaper than
        # the generic Hough/dark-area search. Only use the generic fallback for
        # unusual skins or synthetic/grayscale frames with no title proposal.
        # Production defaults disable this fallback because hotbars and footer
        # panels are dark rectangles too and caused most historical false hits.
        generic_boxes = (
            set(self._candidate_boxes(gray)) if not title_boxes and self.config.allow_generic_fallback else set()
        )
        all_boxes = title_boxes | generic_boxes
        candidate_boxes = [bbox for bbox in all_boxes if self._valid_geometry(bbox, frame_width, frame_height)]
        if not candidate_boxes:
            return None

        edges = cv2.Canny(cv2.GaussianBlur(gray, (3, 3), 0), 20, 80)
        hsv = cv2.cvtColor(color, cv2.COLOR_BGR2HSV)
        hue, saturation, value = cv2.split(hsv)
        yellow_mask = ((hue >= 18) & (hue <= 40) & (saturation >= 110) & (value >= 150)).astype(np.uint8) * 255
        color_mask = ((saturation >= 90) & (value >= 110)).astype(np.uint8) * 255
        dark_integral = cv2.integral((gray < 115).astype(np.float32))
        yellow_integral = cv2.integral((yellow_mask > 0).astype(np.float32))
        color_integral = cv2.integral((color_mask > 0).astype(np.float32))

        def region_mean(integral: np.ndarray, bbox: BBox) -> float:
            x1, y1, x2, y2 = bbox
            area = max(1, (x2 - x1) * (y2 - y1))
            total = integral[y2, x2] - integral[y1, x2] - integral[y2, x1] + integral[y1, x1]
            return float(total / area)

        def title_mean(bbox: BBox) -> float:
            x1, y1, x2, y2 = bbox
            width, height = x2 - x1, y2 - y1
            title_bbox = (
                x1 + round(width * 0.32),
                y1,
                x1 + round(width * 0.78),
                y1 + max(1, round(height * 0.16)),
            )
            return region_mean(yellow_integral, title_bbox)

        def cheap_score(bbox: BBox) -> float:
            dark_ratio = region_mean(dark_integral, bbox)
            yellow_ratio = region_mean(yellow_integral, bbox)
            color_ratio = region_mean(color_integral, bbox)
            title_ratio = title_mean(bbox)
            width, height = bbox[2] - bbox[0], bbox[3] - bbox[1]
            width_score = self._clamp01(1.0 - abs(width / frame_width - 0.392) / 0.10)
            aspect_score = self._clamp01(1.0 - abs(log(max(0.01, width / max(1, height)) / 1.60)) / 0.80)
            border_score, _ = self._border_support(edges, bbox)
            return (
                0.34 * border_score
                + 0.25 * self._clamp01(title_ratio / 0.08)
                + 0.14 * width_score
                + 0.07 * aspect_score
                + 0.08 * self._clamp01((dark_ratio - 0.45) / 0.35)
                + 0.06 * self._clamp01(yellow_ratio / 0.03)
                + 0.06 * self._clamp01(color_ratio / 0.10)
            )

        # Keep every high-value title-derived proposal in contention while
        # bounding expensive text-row scoring for noisy game scenes.
        ranked_title = sorted(title_boxes, key=cheap_score, reverse=True)[:12]
        ranked_generic = sorted(generic_boxes, key=cheap_score, reverse=True)[:12]
        ranked_boxes = []
        for bbox in ranked_title + ranked_generic:
            if bbox in all_boxes and bbox not in ranked_boxes and self._valid_geometry(bbox, frame_width, frame_height):
                ranked_boxes.append(bbox)

        detections: list[TooltipDetection] = []
        for bbox in ranked_boxes:
            detection = self._score(
                gray,
                bbox,
                edges=edges,
                yellow_mask=yellow_mask,
                color_mask=color_mask,
            )
            if detection is not None and self._passes_hard_rules(detection):
                detections.append(detection)
        candidates = self._deduplicate(detections)
        return candidates[0] if candidates else None

    def update(self, image: np.ndarray) -> dict[str, Any]:
        """Track a candidate across frames and expose only stable panels as active."""
        detection = self.detect(image)
        if detection is None:
            self._last_bbox = None
            self._stable_frames = 0
            return {"active": False, "candidate": False}

        if self._last_bbox is not None and self._iou(self._last_bbox, detection.bbox) >= self.config.stability_iou:
            self._stable_frames += 1
        else:
            self._stable_frames = 1
        self._last_bbox = detection.bbox
        active = self._stable_frames >= max(1, self.config.stable_frames)
        return detection.to_signal(active=active, stable_frames=self._stable_frames)
