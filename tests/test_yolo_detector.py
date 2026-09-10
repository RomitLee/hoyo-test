from __future__ import annotations

from pathlib import Path
from typing import ClassVar

import numpy as np

from hoyo_analyzer.yolo_detector import YOLOTooltipDetector


class FakeBoxes:
    def __init__(self, boxes: list[list[float]], scores: list[float], classes: list[int] | None = None) -> None:
        self.xyxy = np.asarray(boxes, dtype=float)
        self.conf = np.asarray(scores, dtype=float)
        self.cls = np.asarray(classes or [0] * len(boxes), dtype=float)

    def __len__(self) -> int:
        return len(self.conf)


class FakeResult:
    names: ClassVar[dict[int, str]] = {0: "equipment_tooltip", 1: "inventory_panel"}

    def __init__(self, boxes: FakeBoxes) -> None:
        self.boxes = boxes


class FakeModel:
    def __init__(self, result: FakeResult | None) -> None:
        self.result = result
        self.calls = 0

    def predict(self, **_kwargs):
        self.calls += 1
        return [self.result] if self.result is not None else []


def test_missing_model_is_reported_without_crashing(tmp_path: Path):
    detector = YOLOTooltipDetector(tmp_path / "missing.pt")
    signal = detector.update(np.zeros((20, 20, 3), dtype=np.uint8))
    assert signal["active"] is False
    assert signal["reason"] == "yolo_model_not_found"


def test_detector_selects_highest_confidence_and_stabilizes(tmp_path: Path):
    model_path = tmp_path / "best.pt"
    model_path.write_bytes(b"fake")
    fake_model = FakeModel(FakeResult(FakeBoxes([[1, 2, 10, 12], [20, 22, 40, 50]], [0.71, 0.93])))
    detector = YOLOTooltipDetector(model_path, stable_frames=2, model_loader=lambda _path: fake_model)
    image = np.zeros((60, 60, 3), dtype=np.uint8)

    first = detector.update(image)
    second = detector.update(image)

    assert first["candidate"] is True
    assert first["active"] is False
    assert second["active"] is True
    assert second["bbox"] == [20, 22, 40, 50]
    assert second["class_name"] == "equipment_tooltip"
    assert fake_model.calls == 2


def test_detection_disappearing_resets_stability(tmp_path: Path):
    model_path = tmp_path / "best.pt"
    model_path.write_bytes(b"fake")
    fake_model = FakeModel(FakeResult(FakeBoxes([[1, 2, 10, 12]], [0.9])))
    detector = YOLOTooltipDetector(model_path, stable_frames=2, model_loader=lambda _path: fake_model)
    image = np.zeros((20, 20, 3), dtype=np.uint8)

    detector.update(image)
    detector.update(image)
    fake_model.result = None
    missing = detector.update(image)
    fake_model.result = FakeResult(FakeBoxes([[1, 2, 10, 12]], [0.9]))
    after_reset = detector.update(image)

    assert missing["active"] is False
    assert after_reset["active"] is False
    assert after_reset["stable_frames"] == 1


def test_detector_returns_both_classes_from_one_inference(tmp_path: Path):
    model_path = tmp_path / "best.pt"
    model_path.write_bytes(b"fake")
    fake_model = FakeModel(FakeResult(FakeBoxes([[1, 2, 10, 12], [20, 22, 40, 50]], [0.91, 0.88], [0, 1])))
    detector = YOLOTooltipDetector(model_path, stable_frames=1, model_loader=lambda _path: fake_model)

    signal = detector.update(np.zeros((60, 60, 3), dtype=np.uint8))

    assert signal["active"] is True
    assert signal["class_name"] == "equipment_tooltip"
    assert signal["detections"]["equipment_tooltip"]["active"] is True
    assert signal["detections"]["inventory_panel"]["active"] is True
    assert fake_model.calls == 1
