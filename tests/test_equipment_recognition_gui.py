import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from hoyo_analyzer.equipment_dialog import EquipmentRecognitionDialog
from hoyo_analyzer.gui import MainWindow


def test_main_window_exposes_equipment_recognition_button(monkeypatch):
    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(MainWindow, "_refresh_windows", lambda self: None)
    monkeypatch.setattr(MainWindow, "_refresh_occlusion_status_async", lambda self: None)
    window = MainWindow()
    try:
        assert window.equipment_recognition_button.text() == "装备识别"
        assert window.equipment_recognition_button.toolTip()
    finally:
        window.close()
        app.processEvents()


def test_equipment_dialog_has_choose_paste_and_recognize_actions(tmp_path: Path):
    app = QApplication.instance() or QApplication([])
    dialog = EquipmentRecognitionDialog(tmp_path)
    try:
        assert dialog.choose_button.text() == "选择图片"
        assert dialog.paste_button.text() == "粘贴图片"
        assert dialog.recognize_button.text() == "开始AI识别"
        assert not dialog.recognize_button.isEnabled()
    finally:
        dialog.close()
        app.processEvents()
