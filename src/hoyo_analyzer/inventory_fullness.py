"""Rule-based inventory fullness detection for the 5 x 4 item grid.

The detector deliberately keeps the first version explainable: it locates the
inventory title bar, projects the fixed grid geometry, and classifies every
cell by how much of the known empty-slot colour remains visible.  Ambiguous
or obstructed panels are reported as ``invalid`` instead of being promoted to
``full``.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

import cv2
import numpy as np


class SlotState(StrEnum):
    EMPTY = "empty"
    OCCUPIED = "occupied"
    UNKNOWN = "unknown"


class InventoryStatus(StrEnum):
    FULL = "full"
    ALMOST_FULL = "almost_full"
    NOT_FULL = "not_full"
    CLOSED = "inventory_closed"
    INVALID = "invalid"


@dataclass(slots=True, frozen=True)
class InventoryFullnessConfig:
    rows: int = 4
    columns: int = 5
    title_match_threshold: float = 0.78
    partial_title_match_threshold: float = 0.88
    empty_colour_bgr: tuple[int, int, int] = (218, 177, 185)
    empty_colour_tolerance: int = 14
    grid_line_colour_bgr: tuple[int, int, int] = (136, 99, 83)
    grid_line_colour_tolerance: int = 10
    empty_ratio_threshold: float = 0.80
    minimum_slot_colour_ratio: float = 0.012
    minimum_valid_line_coverage: float = 0.60
    minimum_fallback_line_coverage: float = 0.25
    almost_full_max_empty: int = 3
    # Coordinates below are measured in the original 672 px title template.
    grid_offset_x: float = 317.5
    grid_offset_y: float = 72.5
    grid_width: float = 317.5
    grid_height: float = 253.75


@dataclass(slots=True)
class SlotResult:
    row: int
    column: int
    bbox: tuple[int, int, int, int]
    state: SlotState
    empty_colour_ratio: float

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["state"] = self.state.value
        result["bbox"] = list(self.bbox)
        return result


@dataclass(slots=True)
class InventoryAnalysis:
    status: InventoryStatus
    confidence: float
    reason: str
    title_bbox: tuple[int, int, int, int] | None = None
    grid_bbox: tuple[int, int, int, int] | None = None
    title_score: float = 0.0
    location_method: str = "none"
    slots: tuple[SlotResult, ...] = ()

    @property
    def empty_count(self) -> int:
        return sum(slot.state is SlotState.EMPTY for slot in self.slots)

    @property
    def occupied_count(self) -> int:
        return sum(slot.state is SlotState.OCCUPIED for slot in self.slots)

    @property
    def unknown_count(self) -> int:
        return sum(slot.state is SlotState.UNKNOWN for slot in self.slots)

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status.value,
            "confidence": self.confidence,
            "reason": self.reason,
            "title_bbox": list(self.title_bbox) if self.title_bbox else None,
            "grid_bbox": list(self.grid_bbox) if self.grid_bbox else None,
            "title_score": self.title_score,
            "location_method": self.location_method,
            "empty_count": self.empty_count,
            "occupied_count": self.occupied_count,
            "unknown_count": self.unknown_count,
            "slots": [slot.to_dict() for slot in self.slots],
        }


class InventoryFullnessDetector:
    """Detect the inventory grid and count its empty cells."""

    def __init__(
        self,
        template_path: str | Path = "assets/templates/inventory_open.png",
        config: InventoryFullnessConfig | None = None,
    ) -> None:
        self.template_path = Path(template_path)
        self.config = config or InventoryFullnessConfig()
        self._title_template: np.ndarray | None = None

    @staticmethod
    def read_image(path: str | Path) -> np.ndarray | None:
        """Read an image even when its Windows path contains non-ASCII text."""

        try:
            encoded = np.fromfile(Path(path), dtype=np.uint8)
        except OSError:
            return None
        return cv2.imdecode(encoded, cv2.IMREAD_COLOR)

    @staticmethod
    def write_image(path: str | Path, image: np.ndarray) -> bool:
        destination = Path(path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        extension = destination.suffix or ".png"
        ok, encoded = cv2.imencode(extension, image)
        if not ok:
            return False
        encoded.tofile(destination)
        return True

    def _load_title_template(self) -> np.ndarray | None:
        if self._title_template is not None:
            return self._title_template
        image = self.read_image(self.template_path)
        if image is None:
            return None
        self._title_template = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        return self._title_template

    def _locate_from_title(
        self, image: np.ndarray
    ) -> tuple[tuple[int, int, int, int], tuple[int, int, int, int], float] | None:
        template = self._load_title_template()
        if template is None:
            return None
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY) if image.ndim == 3 else image
        best_score = -1.0
        best_bbox: tuple[int, int, int, int] | None = None
        best_scale = 1.0
        # The supplied 800 x 600 samples render the 672 px asset at 80%.
        estimated_scale = min(gray.shape[1] / 1000.0, gray.shape[0] / 750.0)
        scales = np.linspace(estimated_scale * 0.90, estimated_scale * 1.10, 7)
        for scale in scales:
            width = max(16, round(template.shape[1] * float(scale)))
            height = max(8, round(template.shape[0] * float(scale)))
            if width > gray.shape[1] or height > gray.shape[0]:
                continue
            candidate = cv2.resize(template, (width, height), interpolation=cv2.INTER_AREA)
            result = cv2.matchTemplate(gray, candidate, cv2.TM_CCOEFF_NORMED)
            _, score, _, location = cv2.minMaxLoc(result)
            if score > best_score:
                best_score = float(score)
                best_scale = float(scale)
                best_bbox = (location[0], location[1], location[0] + width, location[1] + height)
        if best_bbox is None or best_score < self.config.title_match_threshold:
            # Dialogs commonly obscure only part of the inventory title. Match
            # three overlapping title fragments only after the cheap full-title
            # pass fails, then reconstruct the full title origin.
            best_partial_score = -1.0
            best_partial_bbox: tuple[int, int, int, int] | None = None
            best_partial_scale = 1.0
            source_width = template.shape[1]
            for scale in scales:
                width = max(16, round(source_width * float(scale)))
                height = max(8, round(template.shape[0] * float(scale)))
                for source_left, source_right in ((0, 250), (210, 462), (422, source_width)):
                    fragment = template[:, source_left:source_right]
                    fragment_width = max(16, round(fragment.shape[1] * float(scale)))
                    candidate_fragment = cv2.resize(fragment, (fragment_width, height), interpolation=cv2.INTER_AREA)
                    if candidate_fragment.shape[1] > gray.shape[1]:
                        continue
                    partial_result = cv2.matchTemplate(gray, candidate_fragment, cv2.TM_CCOEFF_NORMED)
                    _, partial_score, _, partial_location = cv2.minMaxLoc(partial_result)
                    reconstructed_left = partial_location[0] - round(source_left * float(scale))
                    reconstructed_top = partial_location[1]
                    if partial_score > best_partial_score:
                        best_partial_score = float(partial_score)
                        best_partial_scale = float(scale)
                        best_partial_bbox = (
                            reconstructed_left,
                            reconstructed_top,
                            reconstructed_left + width,
                            reconstructed_top + height,
                        )
            if best_partial_bbox is None or best_partial_score < self.config.partial_title_match_threshold:
                return None
            best_bbox = best_partial_bbox
            best_score = best_partial_score
            best_scale = best_partial_scale
        left, top, _, _ = best_bbox
        grid_bbox = (
            left + round(self.config.grid_offset_x * best_scale),
            top + round(self.config.grid_offset_y * best_scale),
            left + round((self.config.grid_offset_x + self.config.grid_width) * best_scale),
            top + round((self.config.grid_offset_y + self.config.grid_height) * best_scale),
        )
        return best_bbox, grid_bbox, best_score

    def _empty_colour_mask(self, image: np.ndarray) -> np.ndarray:
        target = np.asarray(self.config.empty_colour_bgr, dtype=np.int16)
        difference = np.abs(image.astype(np.int16) - target)
        return np.all(difference <= self.config.empty_colour_tolerance, axis=2).astype(np.uint8)

    def _grid_line_mask(self, image: np.ndarray) -> np.ndarray:
        target = np.asarray(self.config.grid_line_colour_bgr, dtype=np.int16)
        difference = np.abs(image.astype(np.int16) - target)
        return np.all(difference <= self.config.grid_line_colour_tolerance, axis=2).astype(np.uint8)

    def _refine_grid_bbox(self, image: np.ndarray, bbox: tuple[int, int, int, int]) -> tuple[int, int, int, int]:
        """Snap the projected rectangle to the game's one-pixel grid lines."""

        left, top, right, bottom = bbox
        line_mask = self._grid_line_mask(image)
        height, width = line_mask.shape
        approximate_cell_width = (right - left) / self.config.columns
        approximate_cell_height = (bottom - top) / self.config.rows
        best_vertical = (-1.0, left, round(approximate_cell_width))
        best_horizontal = (-1.0, top, round(approximate_cell_height))
        y1, y2 = max(0, top - 4), min(height, bottom + 4)
        x1, x2 = max(0, left - 4), min(width, right + 4)
        for origin in range(max(0, left - 7), min(width, left + 8)):
            for step in range(max(4, round(approximate_cell_width) - 2), round(approximate_cell_width) + 3):
                coordinates = [origin + index * step for index in range(self.config.columns + 1)]
                if coordinates[-1] >= width:
                    continue
                score = float(np.mean([line_mask[y1:y2, x].mean() for x in coordinates]))
                if score > best_vertical[0]:
                    best_vertical = (score, origin, step)
        for origin in range(max(0, top - 7), min(height, top + 8)):
            for step in range(max(4, round(approximate_cell_height) - 2), round(approximate_cell_height) + 3):
                coordinates = [origin + index * step for index in range(self.config.rows + 1)]
                if coordinates[-1] >= height:
                    continue
                score = float(np.mean([line_mask[y, x1:x2].mean() for y in coordinates]))
                if score > best_horizontal[0]:
                    best_horizontal = (score, origin, step)
        _, refined_left, cell_width = best_vertical
        _, refined_top, cell_height = best_horizontal
        return (
            refined_left,
            refined_top,
            refined_left + self.config.columns * cell_width,
            refined_top + self.config.rows * cell_height,
        )

    def _grid_line_coverage(self, image: np.ndarray, bbox: tuple[int, int, int, int]) -> float:
        left, top, right, bottom = bbox
        if not self._inside_image(bbox, image):
            return 0.0
        mask = self._grid_line_mask(image)
        cell_width = (right - left) / self.config.columns
        cell_height = (bottom - top) / self.config.rows
        vertical = []
        for index in range(1, self.config.columns):
            coordinate = round(left + index * cell_width)
            vertical.append(
                max(
                    float(mask[top : bottom + 1, x].mean())
                    for x in range(max(0, coordinate - 2), min(mask.shape[1], coordinate + 3))
                )
            )
        horizontal = []
        for index in range(1, self.config.rows):
            coordinate = round(top + index * cell_height)
            horizontal.append(
                max(
                    float(mask[y, left : right + 1].mean())
                    for y in range(max(0, coordinate - 2), min(mask.shape[0], coordinate + 3))
                )
            )
        return float(np.mean(vertical + horizontal))

    def _grid_outer_vertical_coverage(self, image: np.ndarray, bbox: tuple[int, int, int, int]) -> float:
        """Return the weaker of the left/right grid borders.

        A foreign dialog can use the same lavender fill as an empty slot. The
        outer borders reveal this case because the dialog cuts one of them off.
        """

        left, top, right, bottom = bbox
        mask = self._grid_line_mask(image)
        coverages = []
        for coordinate in (left, right):
            coverages.append(
                max(
                    float(mask[top : bottom + 1, x].mean())
                    for x in range(max(0, coordinate - 2), min(mask.shape[1], coordinate + 3))
                )
            )
        return min(coverages)

    def _locate_from_empty_slots(self, image: np.ndarray) -> tuple[int, int, int, int] | None:
        """Fallback for a panel whose title bar is partly outside the frame.

        This fallback is intentionally conservative and is useful mainly when
        several empty slots expose the distinctive lavender background.
        """

        mask = self._empty_colour_mask(image)
        image_height, image_width = mask.shape
        best_score = -1.0
        best_bbox: tuple[int, int, int, int] | None = None
        base_scale = image_height / 750.0
        for scale in (base_scale * 0.94, base_scale, base_scale * 1.06):
            grid_width = round(self.config.grid_width * scale)
            grid_height = round(self.config.grid_height * scale)
            if grid_width >= image_width or grid_height >= image_height:
                continue
            # Count characteristic slot-colour pixels in each possible grid window.
            integral = cv2.integral(mask)
            sums = (
                integral[grid_height:, grid_width:]
                - integral[:-grid_height, grid_width:]
                - integral[grid_height:, :-grid_width]
                + integral[:-grid_height, :-grid_width]
            )
            _, total, _, location = cv2.minMaxLoc(sums.astype(np.float32))
            ratio = float(total) / float(grid_width * grid_height)
            if ratio > best_score:
                best_score = ratio
                best_bbox = (location[0], location[1], location[0] + grid_width, location[1] + grid_height)
        # Requiring a large exposed area avoids mistaking ordinary lavender UI
        # decorations for a nearly/full inventory grid.
        if best_bbox is None or best_score < 0.30:
            return None
        refined = self._refine_grid_bbox(image, best_bbox)
        if self._grid_line_coverage(image, refined) < self.config.minimum_fallback_line_coverage:
            return None
        return refined

    @staticmethod
    def _inside_image(bbox: tuple[int, int, int, int], image: np.ndarray) -> bool:
        left, top, right, bottom = bbox
        height, width = image.shape[:2]
        return 0 <= left < right <= width and 0 <= top < bottom <= height

    def analyze_grid(
        self,
        image: np.ndarray,
        grid_bbox: tuple[int, int, int, int],
        *,
        title_bbox: tuple[int, int, int, int] | None = None,
        title_score: float = 0.0,
        location_method: str = "provided",
    ) -> InventoryAnalysis:
        """Classify a known grid. This entry point is also useful for tests."""

        if not self._inside_image(grid_bbox, image):
            return InventoryAnalysis(
                InventoryStatus.INVALID,
                0.0,
                "inventory grid is outside the captured frame",
                title_bbox,
                grid_bbox,
                title_score,
                location_method,
            )
        grid_bbox = self._refine_grid_bbox(image, grid_bbox)
        if not self._inside_image(grid_bbox, image):
            return InventoryAnalysis(
                InventoryStatus.INVALID,
                0.0,
                "refined inventory grid is outside the captured frame",
                title_bbox,
                grid_bbox,
                title_score,
                location_method,
            )
        left, top, right, bottom = grid_bbox
        grid_width = right - left
        grid_height = bottom - top
        mask = self._empty_colour_mask(image)
        slots: list[SlotResult] = []
        for row in range(self.config.rows):
            for column in range(self.config.columns):
                x1 = left + round(column * grid_width / self.config.columns)
                x2 = left + round((column + 1) * grid_width / self.config.columns)
                y1 = top + round(row * grid_height / self.config.rows)
                y2 = top + round((row + 1) * grid_height / self.config.rows)
                inset = max(2, round(min(x2 - x1, y2 - y1) * 0.08))
                interior = mask[y1 + inset : y2 - inset, x1 + inset : x2 - inset]
                ratio = float(interior.mean()) if interior.size else 0.0
                if ratio >= self.config.empty_ratio_threshold:
                    state = SlotState.EMPTY
                elif ratio >= self.config.minimum_slot_colour_ratio:
                    state = SlotState.OCCUPIED
                else:
                    state = SlotState.UNKNOWN
                slots.append(SlotResult(row, column, (x1, y1, x2, y2), state, ratio))

        line_coverage = self._grid_line_coverage(image, grid_bbox)
        outer_vertical_coverage = self._grid_outer_vertical_coverage(image, grid_bbox)
        unknown_count = sum(slot.state is SlotState.UNKNOWN for slot in slots)
        empty_count = sum(slot.state is SlotState.EMPTY for slot in slots)
        if line_coverage < self.config.minimum_valid_line_coverage or outer_vertical_coverage < 0.55:
            status = InventoryStatus.INVALID
            reason = (
                "inventory grid is obstructed "
                f"(inner lines {line_coverage:.0%}, outer border {outer_vertical_coverage:.0%})"
            )
            confidence = max(0.0, 1.0 - min(line_coverage, outer_vertical_coverage))
        elif unknown_count:
            status = InventoryStatus.INVALID
            reason = f"{unknown_count} slot(s) are obstructed or cannot be verified"
            confidence = max(0.0, 1.0 - unknown_count / len(slots))
        elif empty_count == 0:
            status = InventoryStatus.FULL
            reason = "all 20 slots are occupied"
            confidence = 0.96
        elif empty_count <= self.config.almost_full_max_empty:
            status = InventoryStatus.ALMOST_FULL
            reason = f"{empty_count} empty slot(s) remain"
            confidence = 0.95
        else:
            status = InventoryStatus.NOT_FULL
            reason = f"{empty_count} empty slots remain"
            confidence = 0.96
        return InventoryAnalysis(
            status,
            confidence,
            reason,
            title_bbox,
            grid_bbox,
            title_score,
            location_method,
            tuple(slots),
        )

    def analyze(self, image: np.ndarray) -> InventoryAnalysis:
        if image.size == 0 or image.ndim != 3:
            return InventoryAnalysis(InventoryStatus.INVALID, 0.0, "image is empty or not a BGR frame")
        located = self._locate_from_title(image)
        if located is not None:
            title_bbox, grid_bbox, score = located
            return self.analyze_grid(
                image,
                grid_bbox,
                title_bbox=title_bbox,
                title_score=score,
                location_method="title_template",
            )
        grid_bbox = self._locate_from_empty_slots(image)
        if grid_bbox is not None:
            return self.analyze_grid(image, grid_bbox, location_method="empty_slot_fallback")
        return InventoryAnalysis(
            InventoryStatus.CLOSED,
            0.90,
            "inventory title and grid were not found",
        )

    @staticmethod
    def annotate(image: np.ndarray, analysis: InventoryAnalysis) -> np.ndarray:
        canvas = image.copy()
        colours = {
            SlotState.EMPTY: (40, 210, 40),
            SlotState.OCCUPIED: (40, 40, 230),
            SlotState.UNKNOWN: (0, 165, 255),
        }
        if analysis.title_bbox:
            x1, y1, x2, y2 = analysis.title_bbox
            cv2.rectangle(canvas, (x1, y1), (x2, y2), (255, 200, 0), 2)
        for slot in analysis.slots:
            x1, y1, x2, y2 = slot.bbox
            cv2.rectangle(canvas, (x1, y1), (x2, y2), colours[slot.state], 2)
        label = (
            f"{analysis.status.value} | empty={analysis.empty_count} "
            f"occupied={analysis.occupied_count} unknown={analysis.unknown_count}"
        )
        cv2.rectangle(canvas, (0, 0), (min(canvas.shape[1] - 1, 590), 34), (20, 20, 20), -1)
        cv2.putText(canvas, label, (8, 24), cv2.FONT_HERSHEY_SIMPLEX, 0.62, (255, 255, 255), 2, cv2.LINE_AA)
        return canvas
