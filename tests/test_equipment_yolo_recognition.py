from pathlib import Path

import cv2
import numpy as np

from hoyo_analyzer.equipment_recognition import EquipmentImageRecognizer
from hoyo_analyzer.equipment_region import EquipmentRegionDetection


def _write_png(path: Path, image: np.ndarray) -> None:
    ok, encoded = cv2.imencode(".png", image)
    assert ok
    encoded.tofile(path)


def test_full_screenshot_uses_yolo_bbox_before_ocr(tmp_path: Path):
    image_path = tmp_path / "完整游戏截图.png"
    image = np.zeros((100, 160, 3), dtype=np.uint8)
    image[20:80, 60:140] = 255
    _write_png(image_path, image)
    seen_shapes: list[tuple[int, int]] = []

    def fake_yolo(frame: np.ndarray) -> EquipmentRegionDetection:
        assert frame.shape[:2] == (100, 160)
        return EquipmentRegionDetection((60, 20, 140, 80), 0.93)

    def fake_ocr(crop: np.ndarray) -> tuple[str, str]:
        seen_shapes.append(crop.shape[:2])
        return "紫香乌金裙\n120级 女衣\n力量 +20", "测试OCR"

    result = EquipmentImageRecognizer(
        ocr_callback=fake_ocr,
        yolo_detector=fake_yolo,
        crop_padding=0,
        region_output_dir=tmp_path / "regions",
    ).recognize(image_path)

    assert result.status == "recognized"
    assert result.detection_bbox == (60, 20, 140, 80)
    assert result.detection_confidence == 0.93
    assert seen_shapes == [(60, 80)]
    assert result.detected_region_path is not None
    assert Path(result.detected_region_path).is_file()


def test_full_screenshot_does_not_send_whole_image_to_ocr_when_yolo_misses(tmp_path: Path):
    image_path = tmp_path / "game.png"
    _write_png(image_path, np.zeros((80, 120, 3), dtype=np.uint8))
    ocr_called = False

    def fake_ocr(_image: np.ndarray) -> tuple[str, str]:
        nonlocal ocr_called
        ocr_called = True
        return "不应被调用", "测试OCR"

    result = EquipmentImageRecognizer(ocr_callback=fake_ocr, yolo_detector=lambda _image: None).recognize(image_path)

    assert result.status == "region_not_found"
    assert not ocr_called
    assert "没有检测到装备属性浮窗" in result.message
