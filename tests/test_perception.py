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
