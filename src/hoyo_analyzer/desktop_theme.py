"""Shared visual theme for the active desktop products."""

from __future__ import annotations

from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QMouseEvent
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QWidget

BACKGROUND = "#070B14"
SURFACE = "#0F172A"
SURFACE_RAISED = "#131E33"
BORDER = "#24324A"
TEXT = "#E6EDF7"
MUTED = "#8EA0B8"
PRIMARY = "#4F8CFF"
PRIMARY_HOVER = "#6EA2FF"
SUCCESS = "#34D399"
WARNING = "#FBBF24"
DANGER = "#FB7185"


DESKTOP_STYLESHEET = f"""
QMainWindow, QDialog {{
    background: {BACKGROUND};
}}
QWidget#windowShell {{
    background: {BACKGROUND};
    border: 1px solid #202D42;
}}
QFrame#customTitleBar {{
    background: #0B1220;
    border: 0;
    border-bottom: 1px solid #202D42;
}}
QLabel#titleBarMark {{
    color: white;
    background: {PRIMARY};
    border-radius: 7px;
    font-size: 11px;
    font-weight: 800;
}}
QLabel#titleBarText {{
    color: #DDE8F7;
    font-size: 12px;
    font-weight: 650;
}}
QPushButton#titleBarButton, QPushButton#titleBarCloseButton {{
    min-width: 38px;
    max-width: 38px;
    min-height: 30px;
    max-height: 30px;
    padding: 0;
    color: #AFC0D6;
    background: transparent;
    border: 0;
    border-radius: 5px;
    font-family: "Segoe UI Symbol", "Microsoft YaHei UI";
    font-size: 14px;
}}
QPushButton#titleBarButton:hover {{
    color: white;
    background: #1A2942;
}}
QPushButton#titleBarCloseButton:hover {{
    color: white;
    background: #C42B3F;
}}
QWidget {{
    color: {TEXT};
    font-family: "Microsoft YaHei UI", "Segoe UI";
    font-size: 12px;
}}
QWidget#appRoot, QWidget#dialogRoot {{
    background: {BACKGROUND};
}}
QLabel {{
    background: transparent;
}}
QGroupBox {{
    background: {SURFACE};
    border: 1px solid {BORDER};
    border-radius: 12px;
    margin-top: 10px;
    padding: 10px;
    font-weight: 700;
    color: #C9D6E8;
}}
QGroupBox::title {{
    subcontrol-origin: margin;
    subcontrol-position: top left;
    left: 12px;
    padding: 0 6px;
    color: #DDE8F7;
}}
QPushButton {{
    min-height: 26px;
    padding: 0 12px;
    color: #DCE7F6;
    background: {SURFACE_RAISED};
    border: 1px solid #30415D;
    border-radius: 7px;
    font-weight: 650;
}}
QPushButton:hover {{
    background: #1A2942;
    border-color: #49617F;
}}
QPushButton:pressed {{
    background: #0C1424;
}}
QPushButton:disabled {{
    color: #53637A;
    background: #0D1422;
    border-color: #1C293B;
}}
QPushButton[role="primary"] {{
    color: white;
    background: {PRIMARY};
    border-color: {PRIMARY};
}}
QPushButton[role="primary"]:hover {{
    background: {PRIMARY_HOVER};
    border-color: {PRIMARY_HOVER};
}}
QPushButton[role="danger"] {{
    color: #FFD9E0;
    background: #3A1826;
    border-color: #6E2941;
}}
QPushButton[role="danger"]:hover {{
    background: #521D31;
    border-color: {DANGER};
}}
QPushButton[role="success"] {{
    color: #D4FFEF;
    background: #0A4B3C;
    border-color: #13745E;
}}
QLineEdit, QComboBox, QSpinBox {{
    min-height: 30px;
    padding: 0 9px;
    color: {TEXT};
    background: #0A1220;
    border: 1px solid #2A3A53;
    border-radius: 7px;
    selection-background-color: {PRIMARY};
}}
QLineEdit:focus, QComboBox:focus, QSpinBox:focus {{
    border: 1px solid {PRIMARY};
}}
QComboBox::drop-down {{
    width: 24px;
    border: 0;
}}
QComboBox QAbstractItemView {{
    color: {TEXT};
    background: {SURFACE_RAISED};
    border: 1px solid #30415D;
    selection-background-color: #244A80;
    outline: 0;
}}
QSlider::groove:horizontal {{
    height: 6px;
    background: #24324A;
    border-radius: 3px;
}}
QSlider::sub-page:horizontal {{
    background: {PRIMARY};
    border-radius: 3px;
}}
QSlider::handle:horizontal {{
    width: 18px;
    height: 18px;
    margin: -6px 0;
    background: #EAF2FF;
    border: 3px solid {PRIMARY};
    border-radius: 9px;
}}
QSlider::handle:horizontal:hover {{
    background: white;
    border-color: {PRIMARY_HOVER};
}}
QCheckBox {{
    spacing: 8px;
    color: {TEXT};
}}
QCheckBox::indicator {{
    width: 17px;
    height: 17px;
    background: #0A1220;
    border: 1px solid #405270;
    border-radius: 5px;
}}
QCheckBox::indicator:hover {{
    border-color: {PRIMARY};
}}
QCheckBox::indicator:checked {{
    background: {PRIMARY};
    border-color: {PRIMARY};
}}
QPlainTextEdit {{
    color: #AFC0D6;
    background: #090F1B;
    border: 1px solid {BORDER};
    border-radius: 8px;
    padding: 5px 8px;
    selection-background-color: #244A80;
}}
QScrollArea {{
    background: transparent;
    border: 0;
}}
QScrollArea > QWidget > QWidget {{
    background: transparent;
}}
QTabWidget::pane {{
    background: #0A111E;
    border: 1px solid #202D42;
    border-radius: 9px;
    top: -1px;
}}
QTabBar {{
    background: transparent;
}}
QTabBar::tab {{
    min-width: 126px;
    min-height: 28px;
    padding: 0 14px;
    margin-right: 4px;
    color: #8EA0B8;
    background: #0B1220;
    border: 1px solid #202D42;
    border-bottom: 0;
    border-top-left-radius: 7px;
    border-top-right-radius: 7px;
    font-weight: 700;
}}
QTabBar::tab:hover {{
    color: #DDE8F7;
    background: #132039;
    border-color: #30496B;
}}
QTabBar::tab:selected {{
    color: white;
    background: #18345C;
    border-color: #3D70AE;
}}
QTabBar::tab:disabled {{
    color: #53637A;
    background: #0A101C;
    border-color: #192437;
}}
QTableWidget {{
    color: #D7E2F1;
    background: #0A111E;
    alternate-background-color: #0E1829;
    border: 1px solid {BORDER};
    border-radius: 9px;
    gridline-color: #1D2A3D;
    selection-background-color: #1E3E6D;
    selection-color: white;
}}
QHeaderView::section {{
    color: #9FB0C7;
    background: #121D30;
    border: 0;
    border-right: 1px solid #223149;
    border-bottom: 1px solid #2A3A53;
    padding: 7px 5px;
    font-weight: 700;
}}
QHeaderView {{
    background: #121D30;
}}
QTableCornerButton::section {{
    background: #121D30;
    border: 0;
}}
QScrollBar:vertical {{
    width: 8px;
    background: transparent;
    margin: 2px;
}}
QScrollBar::handle:vertical {{
    min-height: 28px;
    background: #34445D;
    border-radius: 4px;
}}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
    height: 0;
}}
QMenu {{
    color: {TEXT};
    background: {SURFACE_RAISED};
    border: 1px solid #30415D;
    border-radius: 8px;
    padding: 5px;
}}
QMenu::item {{
    padding: 7px 22px 7px 10px;
    border-radius: 5px;
}}
QMenu::item:selected {{
    background: #244A80;
}}
QToolTip {{
    color: {TEXT};
    background: #17233A;
    border: 1px solid #3A4D69;
    padding: 5px;
}}
QMessageBox {{
    background: {SURFACE};
}}
"""


def apply_desktop_theme(widget: QWidget) -> None:
    """Apply the shared theme to a top-level desktop widget."""
    widget.setStyleSheet(DESKTOP_STYLESHEET)


def pill_style(foreground: str, background: str, border: str | None = None) -> str:
    """Return a compact status-pill style used by dynamic labels."""
    outline = border or background
    return (
        f"color:{foreground};background:{background};border:1px solid {outline};"
        "border-radius:7px;padding:4px 9px;font-weight:700;"
    )


def banner_style(foreground: str, background: str, border: str | None = None) -> str:
    """Return a section banner style used by dashboard summaries."""
    outline = border or background
    return (
        f"color:{foreground};background:{background};border:1px solid {outline};"
        "border-radius:8px;padding:7px 10px;font-weight:700;"
    )


class DarkTitleBar(QFrame):
    """Compact draggable title bar for the fixed-size desktop windows."""

    HEIGHT = 32

    def __init__(self, host_window: QWidget, title: str) -> None:
        super().__init__(host_window)
        self._host_window = host_window
        self._drag_offset: QPoint | None = None
        self.setObjectName("customTitleBar")
        self.setFixedHeight(self.HEIGHT)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 1, 3, 1)
        layout.setSpacing(7)
        mark = QLabel("M")
        mark.setObjectName("titleBarMark")
        mark.setFixedSize(18, 18)
        mark.setAlignment(Qt.AlignmentFlag.AlignCenter)
        mark.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        label = QLabel(title)
        label.setObjectName("titleBarText")
        label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        layout.addWidget(mark)
        layout.addWidget(label)
        layout.addStretch(1)

        minimize = QPushButton("—")
        minimize.setObjectName("titleBarButton")
        minimize.setToolTip("最小化")
        minimize.clicked.connect(host_window.showMinimized)
        close = QPushButton("×")
        close.setObjectName("titleBarCloseButton")
        close.setToolTip("关闭")
        close.clicked.connect(host_window.close)
        layout.addWidget(minimize)
        layout.addWidget(close)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_offset = event.globalPosition().toPoint() - self._host_window.frameGeometry().topLeft()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self._drag_offset is not None and event.buttons() & Qt.MouseButton.LeftButton:
            self._host_window.move(event.globalPosition().toPoint() - self._drag_offset)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        self._drag_offset = None
        super().mouseReleaseEvent(event)
