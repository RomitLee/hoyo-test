from __future__ import annotations

import cv2
import numpy as np

from hoyo_analyzer.inventory_fullness import InventoryFullnessDetector, InventoryStatus, SlotState

GRID_BBOX = (40, 30, 360, 280)


def make_grid(empty_slots: int, obstructed_slot: int | None = None) -> np.ndarray:
    image = np.zeros((320, 410, 3), dtype=np.uint8)
    left, top, right, bottom = GRID_BBOX
    cell_width = (right - left) // 5
    cell_height = (bottom - top) // 4
    empty_colour = (218, 177, 185)
    for index in range(20):
        row, column = divmod(index, 5)
        x1 = left + column * cell_width
        y1 = top + row * cell_height
        x2 = left + (column + 1) * cell_width
        y2 = top + (row + 1) * cell_height
        cv2.rectangle(image, (x1, y1), (x2, y2), empty_colour, -1)
        cv2.rectangle(image, (x1, y1), (x2, y2), (136, 99, 83), 1)
        if index >= empty_slots:
            cv2.circle(image, ((x1 + x2) // 2, (y1 + y2) // 2), 22, (30, 150, 230), -1)
    if obstructed_slot is not None:
        row, column = divmod(obstructed_slot, 5)
        x1 = left + column * cell_width
        y1 = top + row * cell_height
        x2 = left + (column + 1) * cell_width
        y2 = top + (row + 1) * cell_height
        cv2.rectangle(image, (x1, y1), (x2, y2), (12, 12, 12), -1)
    return image


def test_classifies_full_grid() -> None:
    result = InventoryFullnessDetector().analyze_grid(make_grid(0), GRID_BBOX)

    assert result.status is InventoryStatus.FULL
    assert result.occupied_count == 20
    assert result.empty_count == 0


def test_classifies_almost_full_grid() -> None:
    result = InventoryFullnessDetector().analyze_grid(make_grid(2), GRID_BBOX)

    assert result.status is InventoryStatus.ALMOST_FULL
    assert result.empty_count == 2


def test_classifies_not_full_grid() -> None:
    result = InventoryFullnessDetector().analyze_grid(make_grid(7), GRID_BBOX)

    assert result.status is InventoryStatus.NOT_FULL
    assert result.empty_count == 7


def test_obstructed_slot_makes_result_invalid() -> None:
    result = InventoryFullnessDetector().analyze_grid(make_grid(3, obstructed_slot=4), GRID_BBOX)

    assert result.status is InventoryStatus.INVALID
    assert result.slots[4].state is SlotState.UNKNOWN


def test_missing_inventory_is_closed() -> None:
    image = np.zeros((600, 800, 3), dtype=np.uint8)

    result = InventoryFullnessDetector().analyze(image)

    assert result.status is InventoryStatus.CLOSED
