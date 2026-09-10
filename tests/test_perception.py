import numpy as np

from hoyo_analyzer.models import FramePacket
from hoyo_analyzer.perception import RuleBasedPerception, TemplateSpec


def test_rule_based_perception_emits_game_visibility_and_change_signal():
    perception = RuleBasedPerception(change_threshold=0.025)
    first = perception.observe(FramePacket(0, 0, np.zeros((4, 4, 3), dtype=np.uint8)))
    second = perception.observe(FramePacket(1, 200, np.full((4, 4, 3), 255, dtype=np.uint8)))

    assert first.get("game_visible") is True
    assert first.get("frame_changed")["active"] is False
    assert second.get("frame_changed")["active"] is True
    assert second.get("change_score") == 1.0


def test_multiscale_template_signal_detects_scaled_template(tmp_path):
    cv2 = __import__("cv2")
    template = np.zeros((12, 24), dtype=np.uint8)
    template[2:10, 3:21] = 180
    template[4:8, 7:17] = 255
    template_path = tmp_path / "inventory.png"
    encoded, buffer = cv2.imencode(".png", template)
    assert encoded
    buffer.tofile(str(template_path))

    image = np.zeros((80, 120, 3), dtype=np.uint8)
    scaled = cv2.resize(template, (36, 18), interpolation=cv2.INTER_NEAREST)
    image[25:43, 40:76] = scaled[:, :, None]
    perception = RuleBasedPerception(
        [TemplateSpec("inventory_open", str(template_path), threshold=0.9, multiscale=True)]
    )
    result = perception.observe(FramePacket(0, 0, image))

    assert result.get("inventory_open")["active"] is True
    assert result.get("inventory_open")["score"] >= 0.9
    bbox = result.get("inventory_open")["bbox"]
    assert bbox[2] - bbox[0] == 36
    assert bbox[3] - bbox[1] == 18
    assert abs(bbox[0] - 40) <= 1
    assert abs(bbox[1] - 25) <= 1


class AlwaysActiveTooltipDetector:
    def update(self, _image):
        return {"active": True, "candidate": True, "score": 0.95, "bbox": [10, 10, 50, 60]}


def test_tooltip_event_signal_requires_cursor_over_equipment_slot(monkeypatch):
    perception = RuleBasedPerception(equipment_detector=AlwaysActiveTooltipDetector())
    monkeypatch.setattr(
        perception,
        "_template_signals",
        lambda _image: {"inventory_open": {"active": True, "bbox": [100, 50, 770, 77]}},
    )
    image = np.zeros((600, 800, 3), dtype=np.uint8)

    left_slot = perception.observe(FramePacket(0, 0, image, metadata={"cursor_frame_position": (130, 140)}))
    right_items = perception.observe(FramePacket(1, 200, image, metadata={"cursor_frame_position": (520, 140)}))

    assert left_slot.get("equipment_slot_hover")["active"] is True
    assert left_slot.get("equipment_tooltip")["active"] is True
    assert left_slot.get("equipment_tooltip")["equipment_slot"] == "左上"
    assert right_items.get("equipment_slot_hover")["active"] is False
    assert right_items.get("equipment_tooltip")["raw_active"] is True
    assert right_items.get("equipment_tooltip")["active"] is False


class MultiClassTooltipDetector:
    def update(self, _image):
        return {
            "active": False,
            "candidate": False,
            "detections": {
                "equipment_tooltip": {"active": False, "candidate": False},
                "inventory_panel": {"active": True, "candidate": True, "bbox": [100, 50, 770, 570]},
            },
        }


def test_yolo_inventory_panel_signal_is_used_for_slot_gate():
    perception = RuleBasedPerception(equipment_detector=MultiClassTooltipDetector())
    image = np.zeros((600, 800, 3), dtype=np.uint8)

    observation = perception.observe(FramePacket(0, 0, image, metadata={"cursor_frame_position": (130, 140)}))

    assert observation.get("inventory_open")["active"] is True
    assert observation.get("equipment_slot_hover")["active"] is True
