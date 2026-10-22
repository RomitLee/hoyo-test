"""Shared visual theme for the active desktop products."""

from __future__ import annotations

from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QMouseEvent
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QWidget

BACKGROUND = "#F5F7FB"
SURFACE = "#FFFFFF"
SURFACE_RAISED = "#F8FAFD"
BORDER = "#E2E8F0"
TEXT = "#243247"
MUTED = "#66768D"
PRIMARY = "#3478F6"
PRIMARY_HOVER = "#2563DB"
SUCCESS = "#18734D"
WARNING = "#986300"
DANGER = "#B42335"

DESKTOP_STYLESHEET = f"""
QMainWindow, QDialog, QWidget#windowShell, QWidget#appRoot, QWidget#dialogRoot {{ background: {BACKGROUND}; }}
QWidget {{ color: {TEXT}; font-family: "Microsoft YaHei UI"; font-size: 12px; }}
QWidget#windowShell {{ border: 1px solid {BORDER}; }}
QFrame#customTitleBar {{ background: {SURFACE}; border: 0; border-bottom: 1px solid {BORDER}; }}
QLabel {{ background: transparent; }}
QLabel#titleBarMark {{ color: white; background: {PRIMARY}; border-radius: 6px; font-size: 11px; font-weight: 800; }}
QLabel#titleBarText {{ color: {TEXT}; font-size: 12px; font-weight: 650; }}
QPushButton#titleBarButton, QPushButton#titleBarCloseButton {{ min-width: 38px; max-width: 38px; min-height: 30px; max-height: 30px; padding: 0; color: {MUTED}; background: transparent; border: 0; border-radius: 5px; font-size: 14px; }}
QPushButton#titleBarButton:hover {{ background: #EAF0F8; color: {TEXT}; }}
QPushButton#titleBarCloseButton:hover {{ background: #C42B3F; color: white; }}
QGroupBox {{ background: {SURFACE}; border: 1px solid {BORDER}; border-radius: 8px; margin-top: 10px; padding: 8px; font-weight: 700; }}
QGroupBox::title {{ subcontrol-origin: margin; left: 12px; padding: 0 6px; color: {TEXT}; }}
QPushButton {{ min-height: 26px; padding: 0 10px; color: {TEXT}; background: {SURFACE}; border: 1px solid #D6DFEC; border-radius: 6px; font-weight: 650; }}
QPushButton:hover {{ background: #EEF4FF; border-color: #94B6F5; }}
QPushButton:pressed {{ background: #DDEAFF; }}
QPushButton:disabled {{ color: #9AA7B8; background: #F1F4F8; border-color: {BORDER}; }}
QPushButton[role="primary"] {{ color: white; background: {PRIMARY}; border-color: {PRIMARY}; }}
QPushButton[role="primary"]:hover {{ background: {PRIMARY_HOVER}; }}
QPushButton[role="danger"] {{ color: {DANGER}; background: #FFF0F2; border-color: #F2BCC4; }}
QPushButton[role="danger"]:hover {{ background: #FFE2E7; }}
QPushButton[role="success"] {{ color: {SUCCESS}; background: #E8F7EF; border-color: #B6E1CA; }}
QLineEdit, QComboBox, QSpinBox {{ min-height: 30px; padding: 0 9px; color: {TEXT}; background: white; border: 1px solid #D6DFEC; border-radius: 6px; selection-background-color: {PRIMARY}; selection-color: white; }}
QLineEdit:focus, QComboBox:focus, QSpinBox:focus {{ border-color: {PRIMARY}; }}
QComboBox::drop-down {{ width: 24px; border: 0; }}
QComboBox QAbstractItemView {{ background: white; color: {TEXT}; border: 1px solid {BORDER}; selection-background-color: #DDEAFF; }}
QSlider::groove:horizontal {{ height: 6px; background: {BORDER}; border-radius: 3px; }}
QSlider::sub-page:horizontal {{ background: {PRIMARY}; border-radius: 3px; }}
QSlider::handle:horizontal {{ width: 18px; height: 18px; margin: -6px 0; background: white; border: 3px solid {PRIMARY}; border-radius: 9px; }}
QCheckBox {{ spacing: 8px; }}
QCheckBox::indicator {{ width: 17px; height: 17px; background: white; border: 1px solid #AEBFD4; border-radius: 4px; }}
QCheckBox::indicator:checked {{ background: {PRIMARY}; border-color: {PRIMARY}; }}
QPlainTextEdit {{ color: {MUTED}; background: {SURFACE_RAISED}; border: 1px solid {BORDER}; border-radius: 6px; padding: 4px 8px; selection-background-color: #DDEAFF; }}
QScrollArea {{ background: transparent; border: 0; }}
QScrollArea > QWidget > QWidget {{ background: transparent; }}
QTabWidget::pane {{ background: {SURFACE}; border: 1px solid {BORDER}; border-radius: 7px; top: -1px; }}
QTabBar {{ background: transparent; }}
QTabBar::tab {{ min-width: 126px; min-height: 30px; padding: 0 14px; margin-right: 4px; color: {MUTED}; background: #F8FAFD; border: 1px solid {BORDER}; border-bottom: 0; border-top-left-radius: 6px; border-top-right-radius: 6px; font-weight: 700; }}
QTabBar::tab:hover {{ color: {PRIMARY}; background: #EEF4FF; }}
QTabBar::tab:selected {{ color: #2563DB; background: #E8F0FF; border-color: #AAC6FA; }}
QTableWidget {{ color: {TEXT}; background: white; alternate-background-color: #F8FAFD; border: 1px solid {BORDER}; border-radius: 6px; gridline-color: {BORDER}; selection-background-color: #E8F0FF; selection-color: {TEXT}; }}
QHeaderView::section {{ color: #52647D; background: #F1F5FB; border: 0; border-right: 1px solid {BORDER}; border-bottom: 1px solid {BORDER}; padding: 7px 5px; font-weight: 700; }}
QHeaderView, QTableCornerButton::section {{ background: #F1F5FB; border: 0; }}
QScrollBar:vertical {{ width: 8px; background: transparent; margin: 2px; }}
QScrollBar::handle:vertical {{ min-height: 28px; background: #BCC9D9; border-radius: 4px; }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
QMenu, QToolTip {{ color: {TEXT}; background: white; border: 1px solid {BORDER}; border-radius: 6px; padding: 5px; }}
QMenu::item {{ padding: 7px 22px 7px 10px; border-radius: 4px; }}
QMenu::item:selected {{ background: #E8F0FF; }}
QMessageBox {{ background: white; }}
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
