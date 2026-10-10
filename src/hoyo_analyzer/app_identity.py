"""Windows application identity and icon helpers."""

from __future__ import annotations

import ctypes
import os

from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication

from .paths import application_root

SLAVE_APP_ID = "RomitLee.HoyoInventoryMonitor.Slave"
MASTER_APP_ID = "RomitLee.HoyoInventoryMonitor.Master"


def configure_windows_app_id(app_id: str) -> None:
    """Give Windows a stable taskbar identity instead of Python's default."""
    if os.name != "nt":
        return
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(app_id)
    except (AttributeError, OSError):
        pass


def application_icon() -> QIcon:
    """Load the shared product icon in source and packaged installations."""
    return QIcon(str(application_root() / "assets" / "icons" / "hoyo_monitor.ico"))


def apply_application_icon(app: QApplication) -> QIcon:
    """Apply the product icon globally and return it for the main window."""
    icon = application_icon()
    if not icon.isNull():
        app.setWindowIcon(icon)
    return icon
