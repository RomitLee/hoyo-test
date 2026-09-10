"""Locate the equipment slots inside the Fantasy Westward Journey inventory panel.

The inventory title template anchors the panel.  Slot geometry is expressed in
reference-template pixels, so it follows the four supported 4:3 game sizes and
any intermediate window scaling without relying on the user's red annotation.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

BBox = tuple[int, int, int, int]

# ``assets/templates/inventory_open.png`` is 672 x 30.  The eight equipment
# slots are laid out in two columns around the character preview.
_REFERENCE_TITLE_WIDTH = 672.0
_REFERENCE_SLOTS: tuple[tuple[str, BBox], ...] = (
    ("左上", (13, 73, 79, 139)),
    ("左二", (13, 149, 79, 215)),
    ("左三", (13, 224, 79, 290)),
    ("左下", (13, 300, 79, 366)),
    ("右上", (244, 73, 310, 139)),
    ("右二", (244, 149, 310, 215)),
    ("右三", (244, 224, 310, 290)),
    ("右下", (244, 300, 310, 366)),
)


@dataclass(frozen=True, slots=True)
class EquipmentSlotHover:
    """Result of mapping the current mouse position to an equipment slot."""

    active: bool
    cursor: tuple[int, int] | None = None
    slot: str | None = None
    bbox: BBox | None = None
    method: str = "cursor"

    def to_signal(self) -> dict[str, Any]:
        signal: dict[str, Any] = {"active": self.active, "method": self.method}
        if self.cursor is not None:
            signal["cursor"] = list(self.cursor)
        if self.slot is not None:
            signal["slot"] = self.slot
        if self.bbox is not None:
            signal["bbox"] = list(self.bbox)
        return signal


def equipment_slot_boxes(inventory_title_bbox: BBox) -> tuple[tuple[str, BBox], ...]:
    """Return eight slot boxes in source-frame coordinates.

    ``inventory_title_bbox`` is emitted by template matching.  The scale is
    derived from its width because the title template and inventory panel scale
    together.
    """

    title_left, title_top, title_right, _ = inventory_title_bbox
    title_width = max(1, title_right - title_left)
    scale = title_width / _REFERENCE_TITLE_WIDTH
    boxes: list[tuple[str, BBox]] = []
    for name, (x1, y1, x2, y2) in _REFERENCE_SLOTS:
        boxes.append(
            (
                name,
                (
                    round(title_left + x1 * scale),
                    round(title_top + y1 * scale),
                    round(title_left + x2 * scale),
                    round(title_top + y2 * scale),
                ),
            )
        )
    return tuple(boxes)


def detect_equipment_slot_hover(
    inventory_title_signal: Any,
    cursor_position: Any,
    *,
    tolerance_pixels: int = 3,
) -> EquipmentSlotHover:
    """Check whether the cursor is over one of the left-panel equipment slots.

    The right-side item grid is deliberately absent from ``_REFERENCE_SLOTS``;
    therefore hovering a consumable or other item can never satisfy this gate.
    """

    if not isinstance(inventory_title_signal, dict) or not inventory_title_signal.get("active", False):
        return EquipmentSlotHover(False)
    raw_bbox = inventory_title_signal.get("bbox")
    if not isinstance(raw_bbox, (list, tuple)) or len(raw_bbox) != 4:
        return EquipmentSlotHover(False)
    if not isinstance(cursor_position, (list, tuple)) or len(cursor_position) != 2:
        return EquipmentSlotHover(False)

    try:
        title_bbox = tuple(int(value) for value in raw_bbox)
        cursor = (int(cursor_position[0]), int(cursor_position[1]))
    except (TypeError, ValueError):
        return EquipmentSlotHover(False)

    x, y = cursor
    for name, bbox in equipment_slot_boxes(title_bbox):
        x1, y1, x2, y2 = bbox
        if x1 - tolerance_pixels <= x < x2 + tolerance_pixels and y1 - tolerance_pixels <= y < y2 + tolerance_pixels:
            return EquipmentSlotHover(True, cursor, name, bbox)
    return EquipmentSlotHover(False, cursor)
