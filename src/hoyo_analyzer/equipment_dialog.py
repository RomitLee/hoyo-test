"""Dialog for uploading/pasting and recognizing an equipment screenshot."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from PySide6.QtCore import QObject, Qt, QThread, Signal, Slot
from PySide6.QtGui import QCloseEvent, QImage, QPainter, QPixmap
from PySide6.QtWidgets import (
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from .equipment_recognition import EquipmentImageRecognizer, EquipmentRecognitionResult
from .models import BEIJING_TZ


class EquipmentRecognitionWorker(QObject):
    result_ready = Signal(object)
    failed = Signal(str)

    def __init__(self, image_path: str, model_path: Path, region_output_dir: Path) -> None:
        super().__init__()
        self.image_path = image_path
        self.model_path = model_path
        self.region_output_dir = region_output_dir

    @Slot()
    def run(self) -> None:
        try:
            self.result_ready.emit(
                EquipmentImageRecognizer(
                    model_path=self.model_path,
                    region_output_dir=self.region_output_dir,
                ).recognize(self.image_path)
            )
        except Exception as exc:  # noqa: BLE001 - worker must report to dialog
            self.failed.emit(f"{type(exc).__name__}: {exc}")


class EquipmentRecognitionDialog(QDialog):
    """Small modal workflow for an equipment screenshot."""

    recognition_finished = Signal(object)

    def __init__(self, project_root: Path, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.project_root = project_root
        self.selected_image_path: Path | None = None
        self.result: EquipmentRecognitionResult | None = None
        self.recognition_thread: QThread | None = None
        self.recognition_worker: EquipmentRecognitionWorker | None = None
        self.setWindowTitle("装备识别")
        self.setModal(True)
        self.resize(760, 540)
        self._build_ui()

    def _build_ui(self) -> None:
        self.setStyleSheet(
            """
            QDialog { background:#f4f7fb; color:#26324a; }
            QLabel#preview { background:#101522; color:#aeb9d2; border:1px solid #dbe2ef; border-radius:12px; }
            QLabel#resultTitle { color:#4353b8; font-size:18px; font-weight:800; }
            QLabel#hint { color:#7c8aa5; font-size:11px; }
            QPlainTextEdit { background:#ffffff; border:1px solid #e4eaf3; border-radius:10px; }
            QPushButton { min-height:32px; padding:4px 14px; border:0; border-radius:9px; font-weight:700; }
            QPushButton#primary { background:#6c63e8; color:#ffffff; }
            QPushButton#primary:disabled { background:#c8cce0; }
            QPushButton#secondary { background:#e9edff; color:#4c51a3; }
            QPushButton#save { background:#dff7e9; color:#27724b; }
            """
        )
        root = QVBoxLayout(self)
        root.setContentsMargins(14, 14, 14, 14)
        root.setSpacing(10)

        title = QLabel("装备识别")
        title.setObjectName("resultTitle")
        root.addWidget(title)
        hint = QLabel("支持选择游戏完整截图或直接粘贴剪贴板图片（Ctrl+V）。YOLO会先定位装备属性浮窗，再交给OCR读取名称和属性。")
        hint.setObjectName("hint")
        hint.setWordWrap(True)
        root.addWidget(hint)

        body = QHBoxLayout()
        body.setSpacing(12)
        self.preview = QLabel("还没有选择图片")
        self.preview.setObjectName("preview")
        self.preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview.setMinimumSize(350, 330)
        body.addWidget(self.preview, 1)

        right = QVBoxLayout()
        right.setSpacing(8)
        self.result_text = QPlainTextEdit()
        self.result_text.setReadOnly(True)
        self.result_text.setPlaceholderText("识别结果会显示在这里…\n请先训练并放置 YOLO 权重：models/equipment_tooltip/best.pt")
        right.addWidget(self.result_text, 1)
        body.addLayout(right, 1)
        root.addLayout(body, 1)

        actions = QHBoxLayout()
        self.choose_button = QPushButton("选择图片")
        self.choose_button.setObjectName("secondary")
        self.paste_button = QPushButton("粘贴图片")
        self.paste_button.setObjectName("secondary")
        self.recognize_button = QPushButton("开始AI识别")
        self.recognize_button.setObjectName("primary")
        self.save_button = QPushButton("保存识别图片")
        self.save_button.setObjectName("save")
        self.recognize_button.setEnabled(False)
        self.save_button.setEnabled(False)
        actions.addWidget(self.choose_button)
        actions.addWidget(self.paste_button)
        actions.addStretch(1)
        actions.addWidget(self.recognize_button)
        actions.addWidget(self.save_button)
        root.addLayout(actions)

        self.choose_button.clicked.connect(self.choose_image)
        self.paste_button.clicked.connect(self.paste_image)
        self.recognize_button.clicked.connect(self.start_recognition)
        self.save_button.clicked.connect(self.save_card_as)

    def keyPressEvent(self, event: Any) -> None:
        if event.key() == Qt.Key.Key_V and event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            self.paste_image()
            event.accept()
            return
        super().keyPressEvent(event)

    def closeEvent(self, event: QCloseEvent) -> None:
        # Do not destroy a modal dialog while OCR is still executing in its
        # worker thread.  The worker will finish quickly and the dialog can
        # then be closed safely.
        if self.recognition_thread is not None and self.recognition_thread.isRunning():
            QMessageBox.information(self, "正在识别", "识别仍在进行，请稍候再关闭窗口。")
            event.ignore()
            return
        super().closeEvent(event)

    def _set_image(self, image: QImage, path: Path) -> None:
        if image.isNull():
            QMessageBox.warning(self, "图片无效", "无法读取该图片，请换一张图片。")
            return
        self.selected_image_path = path
        self.result = None
        self.save_button.setEnabled(False)
        self.result_text.clear()
        self.preview.setPixmap(QPixmap.fromImage(image).scaled(self.preview.size(), Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
        self.recognize_button.setEnabled(True)

    @Slot()
    def choose_image(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "选择装备图片",
            str(self.project_root),
            "图片文件 (*.png *.jpg *.jpeg *.bmp *.webp)",
        )
        if path:
            self._set_image(QImage(path), Path(path))

    @Slot()
    def paste_image(self) -> None:
        from PySide6.QtWidgets import QApplication

        clipboard_image = QApplication.clipboard().image()
        if clipboard_image.isNull():
            QMessageBox.information(self, "剪贴板没有图片", "请先复制一张装备属性面板图片，再点击“粘贴图片”或按 Ctrl+V。")
            return
        output_dir = self.project_root / "runtime" / "equipment" / "recognition_inputs"
        output_dir.mkdir(parents=True, exist_ok=True)
        path = output_dir / f"clipboard_{datetime.now(BEIJING_TZ).strftime('%Y%m%d_%H%M%S_%f')}.png"
        clipboard_image.save(str(path), "PNG")
        self._set_image(clipboard_image, path)

    @Slot()
    def start_recognition(self) -> None:
        if self.selected_image_path is None or self.recognition_thread is not None:
            return
        self.recognize_button.setEnabled(False)
        self.choose_button.setEnabled(False)
        self.paste_button.setEnabled(False)
        self.result_text.setPlainText("正在识别图片，请稍候…")
        self.recognition_thread = QThread(self)
        self.recognition_worker = EquipmentRecognitionWorker(
            str(self.selected_image_path),
            self.project_root / "models" / "equipment_tooltip" / "best.pt",
            self.project_root / "runtime" / "equipment" / "detected_regions",
        )
        self.recognition_worker.moveToThread(self.recognition_thread)
        self.recognition_thread.started.connect(self.recognition_worker.run)
        self.recognition_worker.result_ready.connect(self._on_result)
        self.recognition_worker.failed.connect(self._on_failed)
        self.recognition_worker.result_ready.connect(self.recognition_thread.quit)
        self.recognition_worker.failed.connect(self.recognition_thread.quit)
        self.recognition_thread.finished.connect(self.recognition_worker.deleteLater)
        self.recognition_thread.finished.connect(self._on_thread_finished)
        self.recognition_thread.start()

    @Slot(object)
    def _on_result(self, result: EquipmentRecognitionResult) -> None:
        self.result = result
        self.result_text.setPlainText(self._format_result(result))
        self._write_result_card(result)
        self.save_button.setEnabled(bool(result.card_path))
        self.recognition_finished.emit(result)

    @Slot(str)
    def _on_failed(self, message: str) -> None:
        self.result_text.setPlainText(f"识别失败：{message}")
        QMessageBox.warning(self, "装备识别失败", message)

    @Slot()
    def _on_thread_finished(self) -> None:
        if self.recognition_thread is not None:
            self.recognition_thread.deleteLater()
        self.recognition_thread = None
        self.recognition_worker = None
        self.choose_button.setEnabled(True)
        self.paste_button.setEnabled(True)
        self.recognize_button.setEnabled(self.selected_image_path is not None)

    def _format_result(self, result: EquipmentRecognitionResult) -> str:
        lines = [
            f"状态：{result.message}",
            f"识别引擎：{result.engine or '未安装'}",
            f"YOLO定位：{self._format_detection(result)}",
            f"装备名称：{result.equipment_name}",
            f"装备类型：{result.category}",
            f"装备等级：{result.level}",
            "",
            "属性：",
        ]
        lines.extend(f"  {name}：{value}" for name, value in result.attributes.items())
        if result.raw_text:
            lines.extend(["", "OCR原文：", result.raw_text])
        if result.detected_region_path:
            lines.extend(["", f"YOLO裁剪区域：{result.detected_region_path}"])
        if result.card_path:
            lines.extend(["", f"识别图片：{result.card_path}"])
        return "\n".join(lines)

    @staticmethod
    def _format_detection(result: EquipmentRecognitionResult) -> str:
        if result.detection_bbox is None:
            return "未执行" if result.detection_model is None else "未检测到装备属性浮窗"
        confidence = f"，置信度 {result.detection_confidence:.1%}" if result.detection_confidence is not None else ""
        return f"已定位（框 {result.detection_bbox}{confidence}）"

    def _write_result_card(self, result: EquipmentRecognitionResult) -> None:
        output_dir = self.project_root / "runtime" / "equipment" / "recognition_cards"
        output_dir.mkdir(parents=True, exist_ok=True)
        path = output_dir / f"equipment_{datetime.now(BEIJING_TZ).strftime('%Y%m%d_%H%M%S_%f')}.png"
        card = QImage(900, 1100, QImage.Format.Format_ARGB32)
        card.fill(0xFFF4F7FB)
        painter = QPainter(card)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.GlobalColor.black)
        painter.setFont(self.font())
        painter.drawText(44, 62, "梦幻西游 · 装备识别结果")
        painter.setPen(Qt.GlobalColor.darkGray)
        painter.drawText(44, 92, result.message)
        if self.selected_image_path:
            source = QPixmap(str(self.selected_image_path))
            if not source.isNull():
                painter.drawPixmap(44, 120, source.scaled(812, 360, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
        if result.detected_region_path:
            region = QPixmap(result.detected_region_path)
            if not region.isNull():
                painter.drawText(44, 510, "YOLO定位的装备属性浮窗")
                painter.drawPixmap(44, 525, region.scaled(380, 250, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
        painter.setPen(Qt.GlobalColor.black)
        y = 810
        rows = [("装备名称", result.equipment_name), ("装备类型", result.category), ("装备等级", result.level)]
        rows.extend(result.attributes.items())
        for name, value in rows:
            painter.drawText(60, y, f"{name}：{value}")
            y += 34
        painter.setPen(Qt.GlobalColor.gray)
        painter.drawText(44, 1060, f"识别时间：{result.recognized_at} · 引擎：{result.engine or '未安装'}")
        painter.end()
        if not card.save(str(path), "PNG"):
            return
        result.card_path = str(path)

    @Slot()
    def save_card_as(self) -> None:
        if self.result is None or not self.result.card_path:
            return
        path, _ = QFileDialog.getSaveFileName(self, "保存装备识别图片", self.result.card_path, "PNG图片 (*.png)")
        if path:
            QImage(self.result.card_path).save(path, "PNG")
