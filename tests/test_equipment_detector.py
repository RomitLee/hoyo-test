import cv2
import numpy as np

from hoyo_analyzer.equipment_detector import OpenCVTooltipDetector, TooltipDetectorConfig
from hoyo_analyzer.evidence import EvidenceWriter
from hoyo_analyzer.models import Event, FramePacket


def synthetic_tooltip():
    image = np.full((600, 800, 3), 180, dtype=np.uint8)
    cv2.rectangle(image, (405, 120), (720, 330), (35, 35, 55), -1)
    cv2.rectangle(image, (405, 120), (720, 330), (210, 225, 215), 3)
    # Five separated yellow glyphs mimic the equipment-name row.
    for x in (525, 540, 555, 570, 585):
        cv2.rectangle(image, (x, 130), (x + 9, 148), (0, 220, 255), -1)
    for index, y in enumerate((175, 200, 225, 250, 275, 300)):
        color = ((0, 220, 255), (80, 230, 80), (255, 210, 50), (230, 230, 230))[index % 4]
        cv2.rectangle(image, (525, y), (680, y + 5), color, -1)
    return image


def synthetic_hotbar_false_positive():
    """A dark, text-like icon bar resembling the historical false positives."""
    image = np.full((600, 800, 3), 165, dtype=np.uint8)
    cv2.rectangle(image, (390, 385), (720, 585), (32, 32, 38), -1)
    cv2.rectangle(image, (390, 385), (720, 585), (130, 145, 155), 2)
    # Yellow icon pixels in the expected title zone make this a meaningful
    # regression: candidate generation succeeds, but the colourful icon grid
    # must be rejected by the strict panel characteristics.
    for x in (500, 515, 530, 545, 560):
        cv2.rectangle(image, (x, 395), (x + 10, 414), (0, 220, 255), -1)
    colors = ((30, 70, 240), (40, 210, 80), (230, 100, 30), (210, 30, 180))
    for row, y in enumerate((435, 480, 525)):
        for column, x in enumerate((430, 480, 530, 580, 630, 680)):
            cv2.rectangle(image, (x, y), (x + 35, y + 35), colors[(row + column) % len(colors)], -1)
            cv2.putText(image, f"F{column + 1}", (x, y + 30), cv2.FONT_HERSHEY_SIMPLEX, 0.35, (245, 245, 245), 1)
    return image


def permissive_config(**overrides):
    values = {
        "min_score": 0.0,
        "stable_frames": 1,
        "require_title": False,
        "min_title_ratio": 0.0,
        "min_dark_ratio": 0.0,
        "max_bright_ratio": 1.0,
        "max_color_ratio": 1.0,
        "min_border_score": 0.0,
        "min_text_lines": 0,
        "allow_generic_fallback": True,
    }
    values.update(overrides)
    return TooltipDetectorConfig(**values)


def test_opencv_tooltip_detector_requires_stable_frames():
    detector = OpenCVTooltipDetector(permissive_config(min_score=0.2, stable_frames=2))
    image = synthetic_tooltip()

    first = detector.update(image)
    second = detector.update(image)

    assert first["candidate"] is True
    assert first["active"] is False
    assert second["active"] is True
    expected = (405, 120, 721, 331)
    assert OpenCVTooltipDetector._iou(tuple(second["bbox"]), expected) >= 0.90
    assert second["text_lines"] >= 2


def test_opencv_tooltip_detector_resets_after_panel_disappears():
    detector = OpenCVTooltipDetector(permissive_config(min_score=0.2, stable_frames=2))
    image = synthetic_tooltip()
    assert detector.update(image)["active"] is False
    assert detector.update(image)["active"] is True

    missing = np.full_like(image, 180)
    assert detector.update(missing) == {"active": False, "candidate": False}


def test_strict_detector_accepts_equipment_panel_features():
    detection = OpenCVTooltipDetector().detect(synthetic_tooltip())

    assert detection is not None
    assert detection.score >= 0.88
    assert detection.title_ratio >= 0.075
    assert detection.dark_ratio >= 0.72
    assert detection.bright_ratio <= 0.16
    assert detection.color_ratio <= 0.25
    assert detection.border_score >= 0.75
    assert detection.text_lines >= 3


def test_strict_detector_rejects_colourful_bottom_hotbar():
    image = synthetic_hotbar_false_positive()

    # Demonstrate that it really is a plausible legacy candidate rather than a
    # blank image that happened to pass the test.
    assert OpenCVTooltipDetector(permissive_config()).detect(image) is not None
    assert OpenCVTooltipDetector().detect(image) is None


def test_signal_exposes_explainable_feature_values():
    signal = OpenCVTooltipDetector(TooltipDetectorConfig(stable_frames=1)).update(synthetic_tooltip())

    assert signal["active"] is True
    assert {
        "title_ratio",
        "yellow_ratio",
        "color_ratio",
        "border_score",
        "supported_border_sides",
    }.issubset(signal)


def test_evidence_writer_saves_tooltip_crop(tmp_path):
    packet = FramePacket(3, 1234, synthetic_tooltip())
    event = Event("evt_1", 1234, "equipment_tooltip_opened", payload={"bbox": [405, 120, 721, 331]})

    path = EvidenceWriter(tmp_path / "evidence").save_equipment_tooltip(event, packet, tmp_path / "tooltips")

    assert path is not None
    assert (tmp_path / "tooltips").exists()
    assert event.payload["tooltip_path"] == path
    saved = cv2.imdecode(np.fromfile(path, dtype=np.uint8), cv2.IMREAD_COLOR)
    assert saved is not None
    assert saved.shape[:2] == (211, 316)
