from pathlib import Path

import cv2
import numpy as np

from hoyo_analyzer.equipment_recognition import EquipmentImageRecognizer, parse_equipment_text


def test_parse_equipment_text_extracts_name_level_category_and_attributes():
    parsed = parse_equipment_text(
        "紫香乌金裙\n120级 女衣\n防御 300\n力量 +20\n敏捷 +15\n"
    )

    assert parsed["equipment_name"] == "紫香乌金裙"
    assert parsed["level"] == "120级"
    assert parsed["category"] == "女衣"
    assert parsed["attributes"] == {"防御": "300", "力量": "+20", "敏捷": "+15"}


def test_recognizer_reads_unicode_path_and_uses_injected_ocr(tmp_path: Path):
    image_path = tmp_path / "装备属性截图-中文.png"
    image = np.full((80, 120, 3), 240, dtype=np.uint8)
    success, encoded = cv2.imencode(".png", image)
    assert success
    encoded.tofile(image_path)

    recognizer = EquipmentImageRecognizer(
        ocr_callback=lambda _image: ("紫香乌金裙\n120级 女衣\n力量 +20", "测试OCR")
    )
    result = recognizer.recognize(image_path)

    assert result.status == "recognized"
    assert result.engine == "测试OCR"
    assert result.equipment_name == "紫香乌金裙"
    assert result.level == "120级"
    assert result.category == "女衣"
    assert result.attributes["力量"] == "+20"
    assert result.image_width == 120
    assert result.image_height == 80


def test_recognizer_returns_invalid_image_for_missing_file(tmp_path: Path):
    result = EquipmentImageRecognizer(ocr_callback=lambda _image: ("", "测试OCR")).recognize(
        tmp_path / "missing.png"
    )

    assert result.status == "invalid_image"
    assert "无法读取图片" in result.message


def test_recognizer_returns_empty_result_when_ocr_finds_no_text(tmp_path: Path):
    image_path = tmp_path / "blank.png"
    image = np.zeros((20, 20, 3), dtype=np.uint8)
    success, encoded = cv2.imencode(".png", image)
    assert success
    encoded.tofile(image_path)

    result = EquipmentImageRecognizer(ocr_callback=lambda _image: ("  \n", "测试OCR")).recognize(image_path)

    assert result.status == "empty_result"
    assert result.engine == "测试OCR"
