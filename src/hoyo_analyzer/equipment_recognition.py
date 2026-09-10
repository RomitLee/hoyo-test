"""Equipment recognition from a full Dream 2 game screenshot.

The production path is deliberately two-stage:

    full screenshot -> YOLO equipment_tooltip crop -> local OCR -> parser

OCR alone is not a reliable full-screen detector.  YOLO locates the tooltip,
then OCR only sees the cropped attribute panel.  A legacy whole-image mode is
kept for unit tests and for users who explicitly inject an OCR callback
without a YOLO model; the desktop dialog always uses the YOLO path.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from .equipment_region import EquipmentRegionDetection, YoloEquipmentRegionDetector
from .models import BEIJING_TZ


@dataclass(slots=True)
class EquipmentRecognitionResult:
    """Structured result returned by full-screenshot recognition."""

    source_path: str
    status: str
    message: str
    engine: str = "none"
    equipment_name: str = "未识别"
    level: str = "未识别"
    category: str = "未识别"
    attributes: dict[str, str] = field(default_factory=dict)
    raw_text: str = ""
    image_width: int = 0
    image_height: int = 0
    recognized_at: str = ""
    card_path: str | None = None
    detection_bbox: tuple[int, int, int, int] | None = None
    detection_confidence: float | None = None
    detection_model: str | None = None
    detected_region_path: str | None = None

    @property
    def is_success(self) -> bool:
        return self.status == "recognized"


OcrCallback = Callable[[np.ndarray], tuple[str, str]]
RegionDetector = Callable[[np.ndarray], EquipmentRegionDetection | None]

ATTRIBUTE_NAMES = (
    "法术伤害", "法术防御", "力量", "体质", "魔力", "耐力", "敏捷", "伤害", "命中", "防御",
    "气血", "速度", "躲避", "灵力", "等级",
)
CATEGORY_NAMES = (
    "武器", "头盔", "发钗", "项链", "铠甲", "女衣", "腰带", "鞋子", "戒指", "耳饰", "手镯", "配饰",
)


def _clean_text(value: str) -> str:
    return re.sub(r"[ \t\r\n]+", " ", value).strip()


def parse_equipment_text(raw_text: str) -> dict[str, Any]:
    """Parse common Dream Westward Journey equipment OCR lines conservatively."""
    lines = [_clean_text(line) for line in raw_text.splitlines() if _clean_text(line)]
    joined = " ".join(lines)
    level_match = re.search(r"(?:等级\s*[:：]?\s*)?(\d{2,3})\s*级", joined)
    if level_match is None:
        level_match = re.search(r"等级\s*[:：]?\s*(\d{2,3})", joined)
    level = f"{level_match.group(1)}级" if level_match else "未识别"

    category = "未识别"
    for candidate in CATEGORY_NAMES:
        if candidate in joined:
            category = candidate
            break

    equipment_name = "未识别"
    for line in lines:
        if any(name in line for name in ATTRIBUTE_NAMES):
            continue
        candidate = re.sub(r"\d{2,3}\s*级", "", line)
        for category_name in CATEGORY_NAMES:
            candidate = candidate.replace(category_name, " ")
        candidate = re.sub(r"[^\u4e00-\u9fffA-Za-z0-9·（）()\- ]", "", candidate).strip()
        if re.search(r"[\u4e00-\u9fff]", candidate) and len(candidate) <= 24:
            equipment_name = candidate
            break

    attributes: dict[str, str] = {}
    for line in lines:
        for name in sorted(ATTRIBUTE_NAMES, key=len, reverse=True):
            match = re.search(rf"{re.escape(name)}\s*[:：]?\s*([+\-]?\d+(?:\.\d+)?)", line)
            if match:
                attributes[name] = match.group(1)
                break
    if not attributes:
        for index, line in enumerate(lines[:8], start=1):
            if line != equipment_name:
                attributes[f"识别文本{index}"] = line
    return {"equipment_name": equipment_name or "未识别", "level": level, "category": category, "attributes": attributes}


@lru_cache(maxsize=1)
def _rapidocr_callback() -> tuple[OcrCallback | None, str | None]:
    try:
        from rapidocr import RapidOCR  # type: ignore[import-not-found]

        engine = RapidOCR()
    except Exception:  # noqa: BLE001 - optional OCR must never break app startup
        return None, None

    def recognize(image: np.ndarray) -> tuple[str, str]:
        result = engine(image)
        texts = getattr(result, "txts", None)
        if texts is not None:
            return "\n".join(str(text) for text in texts if str(text).strip()), "RapidOCR"
        if isinstance(result, tuple):
            result = result[0]
        legacy_texts: list[str] = []
        for item in result or []:
            if len(item) >= 2:
                legacy_texts.append(str(item[1]))
        return "\n".join(legacy_texts), "RapidOCR"

    return recognize, "RapidOCR"


def _tesseract_callback() -> tuple[OcrCallback | None, str | None]:
    try:
        import pytesseract  # type: ignore[import-not-found]
    except ImportError:
        return None, None

    def recognize(image: np.ndarray) -> tuple[str, str]:
        return pytesseract.image_to_string(image, lang="chi_sim+eng"), "Tesseract"

    return recognize, "Tesseract"


def _read_image(path: Path) -> np.ndarray | None:
    """Read an image, including paths containing non-ASCII characters on Windows."""
    try:
        encoded = np.fromfile(path, dtype=np.uint8)
    except OSError:
        return None
    if encoded.size == 0:
        return None
    return cv2.imdecode(encoded, cv2.IMREAD_COLOR)


def _write_image(path: Path, image: np.ndarray) -> bool:
    path.parent.mkdir(parents=True, exist_ok=True)
    suffix = path.suffix or ".png"
    success, encoded = cv2.imencode(suffix, image)
    if not success:
        return False
    encoded.tofile(path)
    return True


class EquipmentImageRecognizer:
    """Recognize equipment from a full screenshot using YOLO then OCR."""

    def __init__(
        self,
        ocr_callback: OcrCallback | None = None,
        *,
        yolo_detector: RegionDetector | None = None,
        model_path: str | Path | None = None,
        yolo_confidence: float = 0.35,
        yolo_image_size: int = 640,
        yolo_device: str = "auto",
        crop_padding: int = 8,
        region_output_dir: str | Path | None = None,
    ) -> None:
        self._ocr_callback = ocr_callback
        self._engine_name = "自定义OCR" if ocr_callback else ""
        self._yolo_detector = yolo_detector
        self._require_region = yolo_detector is not None or model_path is not None
        self.crop_padding = max(0, int(crop_padding))
        self.region_output_dir = Path(region_output_dir) if region_output_dir else None
        self.model_path = str(model_path) if model_path is not None else None
        if yolo_detector is None and model_path is not None:
            self._yolo_detector = YoloEquipmentRegionDetector(
                model_path, confidence=yolo_confidence, image_size=yolo_image_size, device=yolo_device
            )

    def _get_ocr_callback(self) -> OcrCallback | None:
        if self._ocr_callback is not None:
            return self._ocr_callback
        callback, engine_name = _rapidocr_callback()
        if callback is not None:
            self._ocr_callback, self._engine_name = callback, engine_name or "RapidOCR"
            return callback
        callback, engine_name = _tesseract_callback()
        if callback is not None:
            self._ocr_callback, self._engine_name = callback, engine_name or "Tesseract"
            return callback
        return None

    def _detect_and_crop(self, image: np.ndarray) -> tuple[np.ndarray | None, EquipmentRegionDetection | None, str | None]:
        if not self._require_region:
            return image, None, None
        assert self._yolo_detector is not None
        detection = self._yolo_detector(image)
        detector_error = getattr(self._yolo_detector, "last_error", None)
        if detection is None:
            return None, None, detector_error
        height, width = image.shape[:2]
        x1, y1, x2, y2 = detection.bbox
        padding = self.crop_padding
        x1, y1 = max(0, x1 - padding), max(0, y1 - padding)
        x2, y2 = min(width, x2 + padding), min(height, y2 + padding)
        crop = image[y1:y2, x1:x2]
        if crop.size == 0:
            return None, None, "YOLO检测框无效，无法裁剪装备属性浮窗。"
        adjusted = EquipmentRegionDetection((x1, y1, x2, y2), detection.confidence, detection.class_id, detection.class_name)
        return crop, adjusted, None

    def recognize(self, image_path: str | Path) -> EquipmentRecognitionResult:
        path = Path(image_path)
        image = _read_image(path)
        now = datetime.now(BEIJING_TZ).strftime("%Y-%m-%d %H:%M:%S")
        base = {"source_path": str(path), "image_width": 0, "image_height": 0, "recognized_at": now}
        if image is None or image.size == 0:
            return EquipmentRecognitionResult(status="invalid_image", message="无法读取图片，请重新选择 PNG、JPG 或 BMP 图片。", **base)
        base.update(image_width=int(image.shape[1]), image_height=int(image.shape[0]))

        crop, detection, detector_error = self._detect_and_crop(image)
        if crop is None:
            if detector_error and "模型" in detector_error:
                message = f"{detector_error}。请先训练模型并将 best.pt 放入 models/equipment_tooltip/。"
                status = "yolo_unavailable"
            elif detector_error:
                message, status = detector_error, "yolo_failed"
            else:
                message = "整张截图中没有检测到装备属性浮窗，请确认已打开背包并将鼠标移到左侧装备栏。"
                status = "region_not_found"
            return EquipmentRecognitionResult(status=status, message=message, detection_model=self.model_path, **base)

        region_path: str | None = None
        if detection is not None and self.region_output_dir:
            region_file = self.region_output_dir / f"tooltip_{datetime.now(BEIJING_TZ).strftime('%Y%m%d_%H%M%S_%f')}.png"
            if _write_image(region_file, crop):
                region_path = str(region_file)

        callback = self._get_ocr_callback()
        common = {
            "detection_bbox": detection.bbox if detection else None,
            "detection_confidence": detection.confidence if detection else None,
            "detection_model": self.model_path,
            "detected_region_path": region_path,
            **base,
        }
        if callback is None:
            return EquipmentRecognitionResult(
                status="ocr_unavailable",
                message="已定位装备属性浮窗，但当前环境没有安装本地OCR引擎，请安装 equipment 依赖后重试。",
                **common,
            )
        try:
            raw_text, engine_name = callback(crop)
        except Exception as exc:  # noqa: BLE001 - backend errors become UI data
            return EquipmentRecognitionResult(
                status="ocr_failed",
                message=f"OCR识别失败：{type(exc).__name__}: {exc}",
                engine=self._engine_name or "OCR",
                **common,
            )
        parsed = parse_equipment_text(raw_text)
        has_content = bool(raw_text.strip())
        return EquipmentRecognitionResult(
            status="recognized" if has_content else "empty_result",
            message="已用YOLO定位浮窗并完成OCR，请核对名称和属性。" if has_content else "已定位浮窗，但OCR未识别到文字，请换一张清晰截图。",
            engine=engine_name or self._engine_name or "OCR",
            raw_text=raw_text.strip(),
            **common,
            **parsed,
        )
