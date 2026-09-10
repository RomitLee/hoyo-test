from hoyo_analyzer.equipment_slots import detect_equipment_slot_hover, equipment_slot_boxes


def inventory_signal():
    # Reference-size title at the same position as the supplied 999x749 screenshot.
    return {"active": True, "bbox": [162, 166, 834, 196]}


def test_equipment_slot_boxes_follow_inventory_title_anchor():
    boxes = dict(equipment_slot_boxes((162, 166, 834, 196)))

    assert boxes["左上"] == (175, 239, 241, 305)
    assert boxes["右下"] == (406, 466, 472, 532)


def test_cursor_over_left_equipment_slot_is_accepted():
    result = detect_equipment_slot_hover(inventory_signal(), (210, 270))

    assert result.active is True
    assert result.slot == "左上"
    assert result.bbox == (175, 239, 241, 305)


def test_cursor_over_right_item_grid_is_rejected():
    result = detect_equipment_slot_hover(inventory_signal(), (570, 270))

    assert result.active is False
    assert result.slot is None


def test_missing_inventory_or_cursor_is_rejected():
    assert detect_equipment_slot_hover({"active": False}, (210, 270)).active is False
    assert detect_equipment_slot_hover(inventory_signal(), None).active is False
